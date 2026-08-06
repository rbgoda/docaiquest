"""DocAIQ parsing/extraction eval harness — the 'ruler' for Reducto-parity work.

Offline mode (default): scores precomputed predicted/expected JSON pairs listed
in a manifest. Zero external deps, deterministic, CI-runnable.

    cd backend && python -m eval.run                 # uses eval/dataset/manifest.json
    cd backend && python -m eval.run --k 5
    cd backend && python -m eval.run --json          # machine-readable

Live mode (TODO — G2 follow-up): run the real ingestion + extraction + retrieval
pipeline on each source doc and score the output. Gated behind --live (needs a DB
and provider keys). See eval/README.md.

Each case supplies any subset of {fields, reference_text/ocr_text, table_cells,
retrieval}; only the metrics both sides provide are computed.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from eval import scorer


def _load(base: Path, ref):
    if isinstance(ref, str):
        return json.loads((base / ref).read_text())
    return ref or {}


def _mean(xs):
    xs = list(xs)
    return sum(xs) / len(xs) if xs else 0.0


def score_case(case: dict, base: Path, k: int) -> dict:
    exp = _load(base, case.get("expected"))
    pred = _load(base, case.get("predicted"))
    out: dict = {"id": case.get("id"), "doc_type": case.get("doc_type")}
    if "fields" in exp:
        out["fields"] = scorer.field_prf(pred.get("fields", {}), exp["fields"])
    if "reference_text" in exp:
        out["ocr_cer"] = scorer.cer(pred.get("ocr_text", ""), exp["reference_text"])
    if "table_cells" in exp:
        out["table"] = scorer.table_cell_f1(pred.get("table_cells", []), exp["table_cells"])
    if "retrieval" in exp:
        by_q = {q["query"]: q for q in pred.get("retrieval", [])}
        hits, rrs = [], []
        for q in exp["retrieval"]:
            ranked = by_q.get(q["query"], {}).get("ranked", [])
            hits.append(scorer.hit_at_k(ranked, q.get("relevant", []), k))
            rrs.append(scorer.reciprocal_rank(ranked, q.get("relevant", [])))
        out["retrieval"] = {f"hit@{k}": _mean(hits), "mrr": _mean(rrs), "n": len(hits)}
    if "qa" in exp:
        # QA case (R4): exp.qa = {question, expected:[key-facts], must_cite:[ids],
        # should_abstain}. pred.qa = {answer, citations:[ids], evidence, abstained?}.
        qa = exp["qa"]
        pqa = pred.get("qa", {})
        ans = pqa.get("answer", "")
        did_abstain = pqa.get("abstained")
        if did_abstain is None:
            did_abstain = scorer.is_abstention(ans)
        should = bool(qa.get("should_abstain"))
        qout: dict = {"outcome": scorer.abstention_outcome(should, bool(did_abstain))}
        if not should:
            if qa.get("expected"):
                qout["answer"] = scorer.answer_correctness(ans, qa["expected"])
                qout["faithfulness"] = scorer.faithfulness_proxy(pqa.get("evidence", ""), qa["expected"])
            if qa.get("must_cite"):
                qout["citation"] = scorer.citation_recall(pqa.get("citations", []), qa["must_cite"])
        out["qa"] = qout
    return out


def aggregate(results: list[dict], k: int) -> dict:
    def col(*path):
        vals = []
        for r in results:
            cur = r
            for p in path:
                cur = cur.get(p) if isinstance(cur, dict) else None
                if cur is None:
                    break
            if isinstance(cur, (int, float)):
                vals.append(cur)
        return _mean(vals) if vals else None

    qa_cases = [r["qa"] for r in results if "qa" in r]
    confusion: dict[str, int] = {}
    for q in qa_cases:
        confusion[q["outcome"]] = confusion.get(q["outcome"], 0) + 1

    return {
        "field_f1": col("fields", "f1"),
        "field_precision": col("fields", "precision"),
        "field_recall": col("fields", "recall"),
        "ocr_cer": col("ocr_cer"),
        "table_f1": col("table", "f1"),
        f"retrieval_hit@{k}": col("retrieval", f"hit@{k}"),
        "retrieval_mrr": col("retrieval", "mrr"),
        # R4 · QA / faithfulness / abstention
        "qa_answer_correct": col("qa", "answer", "ratio"),
        "qa_faithfulness": col("qa", "faithfulness", "supported"),
        "qa_citation_recall": col("qa", "citation", "recall"),
        "qa_abstention": confusion or None,
        "n_qa": len(qa_cases),
        "n_cases": len(results),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="DocAIQ parsing/extraction eval harness")
    ap.add_argument("--dataset", default=str(Path(__file__).parent / "dataset" / "manifest.json"))
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--json", action="store_true", help="emit machine-readable JSON only")
    ap.add_argument("--live", action="store_true", help="(TODO) run the real pipeline")
    args = ap.parse_args(argv)

    if args.live:
        print("live mode not implemented yet (G2 follow-up) — see eval/README.md", file=sys.stderr)
        return 2

    manifest_path = Path(args.dataset)
    manifest = json.loads(manifest_path.read_text())
    base = manifest_path.parent
    results = [score_case(c, base, args.k) for c in manifest.get("cases", [])]
    agg = aggregate(results, args.k)

    if args.json:
        print(json.dumps({"cases": results, "aggregate": agg}, indent=2))
        return 0

    print(f"DocAIQ eval — {agg['n_cases']} case(s), k={args.k}\n")
    for r in results:
        parts = [f"  {r['id']:<18} ({r.get('doc_type', '?')})"]
        if "fields" in r:
            parts.append(f"field_f1={r['fields']['f1']:.2f}")
        if "ocr_cer" in r:
            parts.append(f"cer={r['ocr_cer']:.2f}")
        if "table" in r:
            parts.append(f"table_f1={r['table']['f1']:.2f}")
        if "retrieval" in r:
            parts.append(f"hit@{args.k}={r['retrieval'][f'hit@{args.k}']:.2f}")
        if "qa" in r:
            seg = f"qa={r['qa']['outcome']}"
            if "answer" in r["qa"]:
                seg += f" ans={r['qa']['answer']['ratio']:.2f}"
            parts.append(seg)
        print("   ".join(parts))
    print("\n  AGGREGATE")
    for key, val in agg.items():
        if key in ("n_cases", "n_qa"):
            continue
        if isinstance(val, dict):
            print(f"    {key:<22} " + ", ".join(f"{kk}={vv}" for kk, vv in val.items()))
        elif val is None:
            print(f"    {key:<22} —")
        else:
            print(f"    {key:<22} {val:.3f}")
    print(f"    {'cases':<22} {agg['n_cases']} ({agg['n_qa']} QA)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
