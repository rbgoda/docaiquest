"""Retrieval eval — A/B two embedders on a real corpus, in-memory (no DB change, no migration).

Method: sample real chunks, generate ONE paraphrased question per chunk whose answer lives in
that chunk (LLM, prompted to avoid the chunk's distinctive words so it tests SEMANTIC recall, not
keyword overlap). For each embedder, embed the whole chunk corpus + the queries at NATIVE dim,
cosine-rank, and score whether the source chunk is retrieved. Reports recall@1/3/5/10 + MRR.

Isolates the VECTOR stage only (no BM25, no reranker) — so it measures the embedder's raw
contribution, an upper bound on the end-to-end benefit. Run in the backend container:
    docker exec -e PYTHONPATH=/app -w /app <backend> python /app/qa/retrieval_eval.py <owner> <N>

Cap is neutralised in-process (separate from the live server).
"""
import random
import sys

import app.cost_guard as _cg
_cg.guard = lambda *a, **k: None

import numpy as np                                             # noqa: E402
import httpx                                                   # noqa: E402
from sqlalchemy import text as _sql                            # noqa: E402
from app.db import SessionLocal, set_current_tenant            # noqa: E402
from app.documents_scope import set_current_owner_user_pk      # noqa: E402
from app.config import get_settings                            # noqa: E402
from app.llm import gateway                                    # noqa: E402
from app import embeddings as emb                              # noqa: E402


def _gen_question(passage: str) -> str:
    s = get_settings()
    model = s.documents_general_fallback_model or s.strong_extract_model
    if "/" not in model:
        model = f"dashscope/{model}"
    sysmsg = (
        "Write ONE natural question a user would ask whose answer is contained in the passage "
        "below. PARAPHRASE — do not reuse the passage's distinctive words, names, or numbers; test "
        "semantic understanding, not keyword overlap. Output ONLY the question.")
    try:
        r = gateway.call(model=model, temperature=0.4, max_tokens=60, messages=[
            gateway.Message(role="system", content=sysmsg),
            gateway.Message(role="user", content=passage[:1200])])
        q = (r.text or "").strip().strip('"').splitlines()[0].strip()
        return q if 8 <= len(q) <= 240 else ""
    except Exception:
        return ""


def _embed_local(texts):
    m = emb._get_st_model()
    v = m.encode([t or " " for t in texts], normalize_embeddings=True, convert_to_numpy=True, batch_size=32)
    return np.asarray(v, dtype=np.float32)


def _embed_dashscope(texts):
    s = get_settings()
    url = s.dashscope_base_url.rstrip("/") + "/embeddings"
    headers = {"Authorization": f"Bearer {s.dashscope_api_key}", "Content-Type": "application/json"}
    out = []
    for i in range(0, len(texts), 10):
        batch = [t or " " for t in texts[i:i + 10]]
        r = httpx.post(url, json={"model": s.dashscope_embed_model, "input": batch}, headers=headers, timeout=60)
        r.raise_for_status()
        out.extend(item["embedding"] for item in r.json().get("data", []))
    a = np.asarray(out, dtype=np.float32)
    a /= (np.linalg.norm(a, axis=1, keepdims=True) + 1e-9)   # L2 normalize → cosine = dot
    return a


def _score(corpus_vecs, query_vecs, target_idx):
    """recall@k + reciprocal rank for each query, given its target chunk index."""
    sims = query_vecs @ corpus_vecs.T          # (nq, nc) cosine
    ranks = []
    for qi, tgt in enumerate(target_idx):
        order = np.argsort(-sims[qi])
        rank = int(np.where(order == tgt)[0][0]) + 1   # 1-indexed rank of the target
        ranks.append(rank)
    ranks = np.array(ranks)
    return {
        "recall@1": float((ranks <= 1).mean()),
        "recall@3": float((ranks <= 3).mean()),
        "recall@5": float((ranks <= 5).mean()),
        "recall@10": float((ranks <= 10).mean()),
        "MRR": float((1.0 / ranks).mean()),
    }


if __name__ == "__main__":
    owner = int(sys.argv[1]) if len(sys.argv) > 1 else 4
    N = int(sys.argv[2]) if len(sys.argv) > 2 else 80
    set_current_owner_user_pk(owner); set_current_tenant("documents")
    db = SessionLocal()
    rows = db.execute(_sql(
        "select dc.text from document_chunks dc join documents d on d.pk=dc.document_pk "
        "where d.owner_user_id=:o and d.tenant_id='documents' and length(dc.text)>=140"
    ), {"o": owner}).all()
    corpus = [r[0] for r in rows]
    print(f"corpus chunks: {len(corpus)}", flush=True)
    random.seed(7)
    sample_idx = random.sample(range(len(corpus)), min(N, len(corpus)))

    # generate a paraphrased question per sampled chunk
    queries, target_idx = [], []
    for ci in sample_idx:
        q = _gen_question(corpus[ci])
        if q:
            queries.append(q); target_idx.append(ci)
    print(f"queries generated: {len(queries)}/{len(sample_idx)}", flush=True)
    target_idx = np.array(target_idx)

    results = {}
    for name, fn in [("local · MiniLM-L6 (384d)", _embed_local),
                     ("dashscope · text-embedding-v4 (1024d)", _embed_dashscope)]:
        try:
            print(f"embedding corpus+queries with {name} …", flush=True)
            cvec = fn(corpus)
            qvec = fn(queries)
            results[name] = _score(cvec, qvec, target_idx)
        except Exception as e:  # noqa: BLE001
            results[name] = {"error": str(e)}
        print(f"  done {name}", flush=True)

    print("\n=== RETRIEVAL A/B (vector stage only; higher = better) ===", flush=True)
    metrics = ["recall@1", "recall@3", "recall@5", "recall@10", "MRR"]
    hdr = "embedder".ljust(40) + "".join(m.rjust(11) for m in metrics)
    print(hdr, flush=True); print("-" * len(hdr), flush=True)
    for name, r in results.items():
        if "error" in r:
            print(name.ljust(40) + "  ERROR: " + r["error"][:60], flush=True); continue
        print(name.ljust(40) + "".join(f"{r[m]*100:10.1f}%" for m in metrics), flush=True)
    print("\nDONE", flush=True)
    db.close()
