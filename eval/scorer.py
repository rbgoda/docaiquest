"""R4 · deterministic, pure-stdlib RAG scorer. No LLM, no deps, CI-safe.

Scores a list of eval cases (the shared ragas_qa shape). The faithfulness *proxy*
checks whether the expected key-facts appear in the cited evidence (contexts) — a
cheap stand-in for Ragas's LLM-judge faithfulness. Also scores an answer-correctness
proxy, citation recall, and the abstention matrix.

Case shape (all optional except question/answer):
  question, answer, contexts:[str], ground_truth, expected:[str],
  citations:[id], must_cite:[id], should_abstain:bool
"""
from __future__ import annotations

import re
import statistics

_WS = re.compile(r"\s+")
_NONALNUM = re.compile(r"[^a-z0-9]+")

# Markers an answer uses when it declines / can't find the evidence. Covers both the
# formal refusal (INSUFFICIENT_EVIDENCE) and the natural-language declines the pipeline
# emits ("Not in this document.", "not stated", …).
_ABSTAIN_MARKERS = ("insufficient_evidence", "insufficient evidence",
                    "not found in the retrieved evidence", "i don't have",
                    "i do not have", "cannot find", "no evidence",
                    "not in this document", "not in the document", "not mentioned",
                    "not stated", "not provided", "not specified", "does not contain",
                    "doesn't contain", "no information")


def _norm(s: str) -> str:
    return _WS.sub(" ", _NONALNUM.sub(" ", (s or "").lower())).strip()


def _contains(hay: str, needle: str) -> bool:
    n = _norm(needle)
    return bool(n) and n in _norm(hay)


def did_abstain(answer: str) -> bool:
    a = (answer or "").lower()
    return any(m in a for m in _ABSTAIN_MARKERS)


def _facts_present(facts, text: str) -> float:
    facts = [f for f in (facts or []) if f]
    if not facts:
        return None
    hit = sum(1 for f in facts if _contains(text, f))
    return hit / len(facts)


def score_case(c: dict) -> dict:
    ctx = "\n".join(c.get("contexts") or [])
    ans = c.get("answer") or ""
    should = bool(c.get("should_abstain"))
    did = did_abstain(ans)

    faith = _facts_present(c.get("expected"), ctx)          # facts in evidence
    ans_corr = _facts_present(c.get("expected"), ans)        # facts in answer

    cited = set(map(str, c.get("citations") or []))
    must = set(map(str, c.get("must_cite") or []))
    cite_recall = (len(cited & must) / len(must)) if must else None

    if should and did:
        abst = "correct_abstain"
    elif should and not did:
        abst = "missed_abstain"      # should have refused — hallucination risk
    elif not should and did:
        abst = "over_abstain"
    else:
        abst = "answered"

    return {"faithfulness_proxy": faith, "answer_correctness_proxy": ans_corr,
            "citation_recall": cite_recall, "abstention": abst}


def _mean(vals):
    vals = [v for v in vals if v is not None]
    return round(statistics.fmean(vals), 4) if vals else None


def score_dataset(cases: list[dict]) -> dict:
    per = [score_case(c) for c in cases]
    matrix = {"correct_abstain": 0, "missed_abstain": 0, "over_abstain": 0, "answered": 0}
    for p in per:
        matrix[p["abstention"]] += 1
    n = len(per) or 1
    # abstention accuracy = decisions that were the right call (answer or refuse).
    correct = matrix["correct_abstain"] + matrix["answered"]
    return {
        "cases": len(per),
        "faithfulness_proxy": _mean(p["faithfulness_proxy"] for p in per),
        "answer_correctness_proxy": _mean(p["answer_correctness_proxy"] for p in per),
        "citation_recall": _mean(p["citation_recall"] for p in per),
        "abstention_accuracy": round(correct / n, 4),
        "abstention_matrix": matrix,
        "_per_case": per,
    }
