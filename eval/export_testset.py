"""Export per-document metadata for a test user from a RUNNING DocAIQ instance into a
durable local fixtures file — so the POC test set (types, trust, extracted fields) is
reusable for deep/regression testing without re-ingesting. PII → gitignored.

  python -m eval.export_testset --email evaltest@example.com --password 'Eval-passw0rd!' \
      --out eval/fixtures/testset_meta.json
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

import httpx


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="eval.export_testset")
    ap.add_argument("--base", default="http://localhost:8085/api")
    ap.add_argument("--email", required=True)
    ap.add_argument("--password", default="Eval-passw0rd!")
    ap.add_argument("--out", type=Path, default=Path("eval/fixtures/testset_meta.json"))
    a = ap.parse_args(argv)

    c = httpx.Client(base_url=a.base, timeout=60.0, follow_redirects=True)
    if c.post("/auth/login", json={"email": a.email, "password": a.password}).status_code != 200:
        raise SystemExit("login failed")
    docs = c.get("/documents").json()
    rows = docs.values() if isinstance(docs, dict) else docs

    out, types = [], Counter()
    for d in rows:
        ef = d.get("extractedFields") or d.get("extracted_fields") or {}
        fields = ef.get("fields") if isinstance(ef, dict) else {}
        trust = (ef.get("trust") or {}).get("score") if isinstance(ef.get("trust"), dict) else d.get("trustScore")
        dt = (fields or {}).get("detected_doc_type") or d.get("docType") or d.get("doc_type") or "unknown"
        types[dt] += 1
        out.append({
            "name": d.get("name"), "docId": d.get("id") or d.get("idExternal"),
            "docType": d.get("docType") or d.get("doc_type"), "detectedType": (fields or {}).get("detected_doc_type"),
            "trustScore": trust, "ingestionStatus": d.get("ingestionStatus") or d.get("ingestion_status"),
            "fieldCount": len(fields) if isinstance(fields, dict) else 0,
            "fields": fields, "fieldConfidence": ef.get("field_confidence") or {},
        })
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps({"user": a.email, "docs": out,
                                 "typeCoverage": dict(types.most_common())}, indent=2, ensure_ascii=False))
    print(f"exported {len(out)} docs · {len(types)} distinct types → {a.out}")
    for t, n in types.most_common():
        print(f"  {n:>2}  {t}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
