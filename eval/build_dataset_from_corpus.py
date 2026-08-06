"""Convert a faithfulness-corpus export (JSONL from
`GET /api/superadmin/faithfulness.jsonl`) into the ragas_qa dataset shape the harness
scores. Bridges the live corpus (consented free chats) → the eval harness.

  python -m eval.build_dataset_from_corpus faithfulness_corpus.jsonl --out eval/dataset/live_qa.json
  # then: python -m eval.ragas_eval --dataset eval/dataset/live_qa.json --judge dashscope

A 👎 case's `suggestion` becomes `ground_truth`; `contexts` come from the cited quotes;
`should_abstain` mirrors the answer's abstained flag (excluded from Ragas answerable metrics).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def convert(records: list[dict], *, labeled_only: bool = False) -> list[dict]:
    out: list[dict] = []
    for r in records:
        if labeled_only and not r.get("label"):
            continue
        out.append({
            "question": r.get("question", ""),
            "answer": r.get("answer", ""),
            "contexts": [c for c in (r.get("contexts") or []) if c],
            "ground_truth": r.get("groundTruth") or "",
            "expected": [],                                  # weak labels — no key-facts
            "should_abstain": bool(r.get("abstained")),
            "_label": r.get("label"), "_category": r.get("category"),
        })
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="eval.build_dataset_from_corpus")
    ap.add_argument("jsonl", type=Path, help="faithfulness_corpus.jsonl export")
    ap.add_argument("--out", type=Path, default=Path("eval/dataset/live_qa.json"))
    ap.add_argument("--labeled-only", action="store_true")
    a = ap.parse_args(argv)

    records = [json.loads(ln) for ln in a.jsonl.read_text().splitlines() if ln.strip()]
    cases = convert(records, labeled_only=a.labeled_only)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps({"cases": cases}, indent=2, ensure_ascii=False))
    print(f"wrote {len(cases)} cases → {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
