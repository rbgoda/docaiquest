"""Generate a real eval dataset + coverage report by driving a RUNNING DocAIQ instance
over a folder of documents: (login | register) → consent → upload → wait ready+extracted →
chat over a use-case question bank, collecting {question, answer, contexts}. Local-first.

  # reuse an unlimited (enterprise) user so the trial 7-doc cap doesn't throttle:
  python -m eval.gen_from_testdata ~/Downloads/testdata --email evaltest@example.com \
      --password 'Eval-passw0rd!' --out eval/dataset/testdata_qa.json --limit 50
  python -m eval.ragas_eval --dataset eval/dataset/testdata_qa.json --dry

Prints a coverage summary (doc types seen · fields extracted · abstention). No ground-truth
labels → `expected`/`ground_truth` empty; scores the reference-free metrics.
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import time
from collections import Counter
from pathlib import Path

import httpx

# 4 use cases per doc: classify/source · extract · summarize · abstention probe.
_QUESTIONS = [
    ("What kind of document is this, and who issued or sent it?", False),
    ("What are the most important dates, amounts, and identifiers in this document?", False),
    ("Give a two-sentence summary of this document.", False),
    ("What is the flight number and boarding gate on this document?", True),
]


def _auth(c: httpx.Client, email: str | None, password: str) -> str:
    """Login if the user exists, else register. Returns the email used."""
    email = email or f"eval-{int(time.time())}@example.com"
    r = c.post("/auth/login", json={"email": email, "password": password})
    if r.status_code == 200:
        return email
    r = c.post("/auth/register", json={"email": email, "password": password,
                                       "name": "Eval Tester", "consent": True})
    if r.status_code not in (200, 201):
        raise SystemExit(f"auth failed: login+register both failed ({r.status_code} {r.text[:120]})")
    return email


def _wait_ready(c: httpx.Client, doc_id: str, timeout: int) -> tuple[str, dict, dict]:
    t0, status, fields, meta = time.time(), None, {}, {}
    while time.time() - t0 < timeout:
        gj = c.get(f"/documents/{doc_id}").json() or {}
        status = gj.get("ingestionStatus") or gj.get("ingestion_status")
        ef = gj.get("extractedFields") or gj.get("extracted_fields") or {}
        fields = ef.get("fields") if isinstance(ef.get("fields"), dict) else {}
        meta = {"doc_type": gj.get("docType") or gj.get("doc_type") or ef.get("doc_type"),
                "detected": (fields or {}).get("detected_doc_type"),
                "trust": (ef.get("trust") or {}).get("score") if isinstance(ef.get("trust"), dict) else gj.get("trustScore")}
        if status == "failed" or (status == "ready" and fields):
            break
        time.sleep(3)
    return status, fields, meta


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="eval.gen_from_testdata")
    ap.add_argument("folder", type=Path)
    ap.add_argument("--base", default="http://localhost:8085/api")
    ap.add_argument("--email", default=None, help="reuse this user (login-or-register)")
    ap.add_argument("--password", default="Eval-passw0rd!")
    ap.add_argument("--out", type=Path, default=Path("eval/dataset/testdata_qa.json"))
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--ready-timeout", type=int, default=240)
    a = ap.parse_args(argv)

    c = httpx.Client(base_url=a.base, timeout=180.0, follow_redirects=True)
    email = _auth(c, a.email, a.password)
    c.post("/me/consent", json={"kind": "personal_data"})
    c.post("/me/consent", json={"kind": "model_training"})
    print(f"session: {email}")

    files = sorted(p for p in a.folder.iterdir() if p.is_file() and not p.name.startswith("."))[:a.limit]
    cases: list[dict] = []
    types: Counter = Counter()
    field_counts: list[int] = []
    ok_docs = 0
    for f in files:
        mime = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
        up = c.post("/documents", files={"file": (f.name, f.read_bytes(), mime)})
        if up.status_code not in (200, 201):
            print(f"  skip {f.name}: upload {up.status_code} {up.text[:100]}"); continue
        doc_id = up.json().get("id") or up.json().get("idExternal") or up.json().get("id_external")
        status, fields, meta = _wait_ready(c, doc_id, a.ready_timeout)
        if status != "ready":
            print(f"  skip {f.name}: status={status}"); continue
        ok_docs += 1
        dtype = meta["detected"] or meta["doc_type"] or "unknown"
        types[dtype] += 1
        field_counts.append(len(fields) if isinstance(fields, dict) else 0)
        print(f"  {f.name[:40]:42} type={dtype:24} fields={len(fields):<3} trust={meta['trust']}")
        doc_facts = json.dumps(fields, ensure_ascii=False)[:1500] if fields else ""
        for q, should_abstain in _QUESTIONS:
            m = c.post(f"/documents/{doc_id}/chat/messages", json={"text": q})
            if m.status_code != 200:
                continue
            j = m.json()
            contexts = [ct.get("quote") for ct in (j.get("citations") or []) if ct.get("quote")]
            if not contexts and doc_facts:
                contexts = [doc_facts]
            cases.append({"question": q, "answer": j.get("text", ""), "contexts": contexts,
                          "ground_truth": "", "expected": [], "should_abstain": should_abstain,
                          "_doc": f.name, "_type": dtype, "_meta": j.get("meta")})

    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps({"cases": cases}, indent=2, ensure_ascii=False))

    print("\n" + "=" * 60)
    print(f"COVERAGE · {ok_docs} docs · {len(cases)} cases · {len(types)} distinct types")
    print(f"  avg fields/doc: {round(sum(field_counts)/max(len(field_counts),1),1)}")
    print("  types seen:")
    for t, n in types.most_common():
        print(f"    {n:>2}  {t}")
    print(f"\nwrote {len(cases)} cases → {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
