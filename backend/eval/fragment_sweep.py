"""#3 dispatcher sweep — run the whole 1088-question bank through the answer-fragment
router (services/answer_fragments) with ZERO LLM calls, and report:

  * shape distribution across all questions
  * token savings vs the legacy all-rules block
  * SANITY failures — questions whose obvious shape keyword didn't route to the
    expected fragment (these are the ones to eyeball)

Run:  cd backend && python -m eval.fragment_sweep [--json out.json]
Pure stdlib. No provider key, no network.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# repo root = backend/.. ; qa bank lives at <root>/qa/qa_data.json
_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "backend"))

from app.services.answer_fragments import (  # noqa: E402
    BASE_RULES, FRAGMENTS, select_answer_fragments, expected_format,
)

_LEGACY_RULE_COUNT = len(BASE_RULES) + len(FRAGMENTS)  # the fixed block sends all of them

# Sanity oracles: if the question matches LHS, the router MUST include the RHS fragment.
_ORACLES = [
    ("compare",  re.compile(r"\b(compare|across all|side by side|both .* different|"
                            r"which is (?:my|the) (?:oldest|newest|largest))\b", re.I)),
    ("of_kind",  re.compile(r"\b(which documents|list all my|how many .* (?:documents|"
                            r"invoices|passports|ids|statements))\b", re.I)),
    ("single",   re.compile(r"\b(what is the (?:balance|total|amount|due date)|how much "
                            r"total|when is .* (?:due|expir))\b", re.I)),
]


def load_questions() -> list[tuple[str, str]]:
    d = json.loads((_ROOT / "qa" / "qa_data.json").read_text())
    out: list[tuple[str, str]] = []
    for cat, qs in d["questions"].items():
        if isinstance(qs, list):
            for q in qs:
                if isinstance(q, str) and q.strip():
                    out.append((cat, q.strip()))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="eval.fragment_sweep")
    ap.add_argument("--json", type=Path, default=None)
    ap.add_argument("--show-failures", type=int, default=25)
    args = ap.parse_args(argv)

    questions = load_questions()
    n = len(questions)
    shape_counts: dict[str, int] = {}
    frag_counts: dict[str, int] = {}
    rules_saved = 0
    base_only = 0
    failures: list[dict] = []
    rows: list[dict] = []

    for cat, q in questions:
        picks = select_answer_fragments(q)
        shape = expected_format(q)
        shape_counts[shape] = shape_counts.get(shape, 0) + 1
        for p in picks:
            frag_counts[p] = frag_counts.get(p, 0) + 1
        included = len(BASE_RULES) + len(picks)
        rules_saved += (_LEGACY_RULE_COUNT - included)
        if not picks:
            base_only += 1
        # sanity oracles
        for need, rx in _ORACLES:
            if rx.search(q) and need not in picks:
                failures.append({"cat": cat, "q": q, "expected_fragment": need, "got": picks})
        rows.append({"cat": cat, "q": q, "picks": picks, "shape": shape})

    avg_included = (sum(len(BASE_RULES) + len(select_answer_fragments(q)) for _, q in questions) / n)
    tok_pct = 100.0 * (1 - avg_included / _LEGACY_RULE_COUNT)

    print(f"\n#3 fragment-dispatcher sweep — {n} questions across {len(set(c for c,_ in questions))} categories\n")
    print("Shape distribution:")
    for s, c in sorted(shape_counts.items(), key=lambda x: -x[1]):
        print(f"  {s:<14} {c:>5}  ({100*c/n:4.1f}%)")
    print("\nFragment usage (how often each shape-rule is included):")
    for f, c in sorted(frag_counts.items(), key=lambda x: -x[1]):
        print(f"  {f:<12} {c:>5}  ({100*c/n:4.1f}%)")
    print(f"\nBase-only (no shape fragment): {base_only}  ({100*base_only/n:.1f}%)")
    print(f"Avg rules/prompt: {avg_included:.2f}  (legacy always sends {_LEGACY_RULE_COUNT})")
    print(f"→ ~{tok_pct:.0f}% fewer rule-lines per prompt on average")
    print(f"\nSanity-oracle failures (obvious keyword → fragment not routed): {len(failures)}  "
          f"({100*len(failures)/n:.2f}%)")
    for f in failures[:args.show_failures]:
        print(f"  ✗ [{f['expected_fragment']}] {f['q'][:70]!r} → got {f['got']}")
    if len(failures) > args.show_failures:
        print(f"  …and {len(failures) - args.show_failures} more")

    if args.json:
        args.json.write_text(json.dumps({
            "n": n, "shape_counts": shape_counts, "frag_counts": frag_counts,
            "base_only": base_only, "avg_included": avg_included,
            "legacy_rules": _LEGACY_RULE_COUNT, "token_reduction_pct": tok_pct,
            "failures": failures, "rows": rows,
        }, indent=2))
        print(f"\nwrote {args.json}")
    # non-zero exit if the router misfires on > 2% of obvious cases
    return 1 if len(failures) > 0.02 * n else 0


if __name__ == "__main__":
    raise SystemExit(main())
