"""Reranker A/B — CPU MiniLM cross-encoder vs an LLM reranker (gpt-4o-mini, one listwise call).

For synthetic query→source-chunk pairs, build a realistic candidate pool (cosine top-K over the
DashScope embedding_v2 column, with the true chunk guaranteed present), rerank it BOTH ways, and
measure where the true chunk lands (recall@1/3/5 + MRR). Isolates the RERANK stage (pool held
constant). Run in the backend container:
    docker exec -e PYTHONPATH=/app -w /app <backend> python /app/qa/reranker_eval.py <owner> <N>
Cap neutralised in-process.
"""
import json
import random
import sys

import app.cost_guard as _cg
_cg.guard = lambda *a, **k: None

import numpy as np                                            # noqa: E402
from sqlalchemy import text as _sql                          # noqa: E402
from app.db import SessionLocal, set_current_tenant          # noqa: E402
from app.documents_scope import set_current_owner_user_pk    # noqa: E402
from app.config import get_settings                          # noqa: E402
from app.llm import gateway                                  # noqa: E402
from app import embeddings as emb, reranker                  # noqa: E402

POOL = 20
LLM_MODEL = "openrouter/openai/gpt-4o-mini"


def _gen_question(passage):
    s = get_settings()
    m = s.documents_general_fallback_model or s.strong_extract_model
    if "/" not in m:
        m = f"dashscope/{m}"
    try:
        r = gateway.call(model=m, temperature=0.4, max_tokens=60, messages=[
            gateway.Message(role="system", content="Write ONE natural question whose answer is in the "
                "passage. Paraphrase — don't reuse its distinctive words. Output only the question."),
            gateway.Message(role="user", content=passage[:1200])])
        q = (r.text or "").strip().strip('"').splitlines()[0].strip()
        return q if 8 <= len(q) <= 240 else ""
    except Exception:
        return ""


def _llm_rerank(query, candidates):
    """gpt-4o-mini listwise reranker — one call, scores every passage 0-10. candidates=[(pk,text)].
    Returns [(pk, score)] desc. Fail-open → input order."""
    docs = "\n".join(f"[{i}] {t[:320]}" for i, (_, t) in enumerate(candidates))
    sysmsg = ("Score how well each numbered PASSAGE answers the QUERY, 0-10 (10=directly answers). "
              "Return ONLY a JSON array of [index, score] pairs for every passage, e.g. [[0,7],[1,2]].")
    try:
        r = gateway.call(model=LLM_MODEL, temperature=0.0, max_tokens=500, messages=[
            gateway.Message(role="system", content=sysmsg),
            gateway.Message(role="user", content=f"QUERY: {query}\n\nPASSAGES:\n{docs}\n\nJSON:")])
        txt = (r.text or "").strip()
        import re
        txt = re.sub(r"^```(?:json)?|```$", "", txt, flags=re.I | re.M).strip()
        pairs = json.loads(txt)
        score = {int(i): float(sc) for i, sc in pairs}
    except Exception:
        score = {}
    ranked = sorted(range(len(candidates)), key=lambda i: -score.get(i, -1))
    return [(candidates[i][0], score.get(i, 0.0)) for i in ranked]


def _pool_for(db, tenant, owner, qvec, target_pk):
    """cosine top-POOL over embedding_v2 for one owner; guarantee target_pk is included."""
    lit = "[" + ",".join(f"{x:.6f}" for x in qvec) + "]"
    rows = db.execute(_sql(f"""
        select dc.pk, dc.text from document_chunks dc join documents d on d.pk=dc.document_pk
        where d.owner_user_id=:o and dc.embedding_v2 is not null
        order by dc.embedding_v2 <=> '{lit}' limit :k"""), {"o": owner, "k": POOL}).all()
    pool = [(r[0], r[1]) for r in rows]
    if target_pk not in [p for p, _ in pool]:
        t = db.execute(_sql("select pk, text from document_chunks where pk=:p"), {"p": target_pk}).first()
        if t:
            pool = pool[:POOL - 1] + [(t[0], t[1])]
    return pool


def _rank_of(ranked, target_pk):
    for i, (pk, _) in enumerate(ranked):
        if pk == target_pk:
            return i + 1
    return len(ranked) + 1


def _metrics(ranks):
    r = np.array(ranks)
    return {"recall@1": float((r <= 1).mean()), "recall@3": float((r <= 3).mean()),
            "recall@5": float((r <= 5).mean()), "MRR": float((1.0 / r).mean())}


if __name__ == "__main__":
    owner = int(sys.argv[1]) if len(sys.argv) > 1 else 4
    N = int(sys.argv[2]) if len(sys.argv) > 2 else 60
    set_current_owner_user_pk(owner); set_current_tenant("documents")
    db = SessionLocal()
    rows = db.execute(_sql("select dc.pk, dc.text from document_chunks dc join documents d on "
        "d.pk=dc.document_pk where d.owner_user_id=:o and dc.embedding_v2 is not null and "
        "length(dc.text)>=140"), {"o": owner}).all()
    corpus = [(r[0], r[1]) for r in rows]
    print(f"chunks: {len(corpus)}", flush=True)
    random.seed(11)
    sample = random.sample(corpus, min(N, len(corpus)))
    mini_ranks, llm_ranks, done = [], [], 0
    for pk, txt in sample:
        q = _gen_question(txt)
        if not q:
            continue
        [qv] = emb.embed_v2([q])
        pool = _pool_for(db, "documents", owner, qv, pk)
        mini_ranks.append(_rank_of(reranker.rerank(q, pool), pk))
        llm_ranks.append(_rank_of(_llm_rerank(q, pool), pk))
        done += 1
        if done % 10 == 0:
            print(f"  {done}…", flush=True)
    print(f"\n=== RERANKER A/B ({done} queries; pool={POOL}; higher=better) ===", flush=True)
    ms = ["recall@1", "recall@3", "recall@5", "MRR"]
    hdr = "reranker".ljust(34) + "".join(m.rjust(11) for m in ms)
    print(hdr, flush=True); print("-" * len(hdr), flush=True)
    for name, ranks in [("CPU · ms-marco-MiniLM-L6", mini_ranks), ("LLM · gpt-4o-mini", llm_ranks)]:
        m = _metrics(ranks)
        print(name.ljust(34) + "".join(f"{m[k]*100:10.1f}%" for k in ms), flush=True)
    print("DONE", flush=True)
    db.close()
