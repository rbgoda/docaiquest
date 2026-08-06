"""Offline tests for the R4 QA / faithfulness / abstention scorers."""
from __future__ import annotations

from eval import scorer as s


def test_is_abstention():
    assert s.is_abstention("INSUFFICIENT_EVIDENCE — nope")
    assert s.is_abstention("  INSUFFICIENT_EVIDENCE ...")
    assert not s.is_abstention("The total is 12420.")
    assert not s.is_abstention("")


def test_answer_correctness():
    r = s.answer_correctness("The total due is 12420 USD.", ["12420", "usd"])
    assert r["correct"] is True and r["ratio"] == 1.0
    r2 = s.answer_correctness("The total is unknown.", ["12420"])
    assert r2["correct"] is False and r2["ratio"] == 0.0
    # single string accepted
    assert s.answer_correctness("invoice EA07", "ea07")["correct"] is True


def test_citation_recall():
    assert s.citation_recall(["c2", "c9"], ["c2"])["recall"] == 1.0
    assert s.citation_recall(["c9"], ["c2", "c5"])["recall"] == 0.0
    assert s.citation_recall(["c5"], ["c2", "c5"])["recall"] == 0.5
    assert s.citation_recall([], [])["recall"] == 1.0  # nothing required


def test_faithfulness_proxy():
    assert s.faithfulness_proxy("Total Due 12420 USD", ["12420"])["supported"] == 1.0
    assert s.faithfulness_proxy("nothing relevant here", ["12420"])["supported"] == 0.0
    assert s.faithfulness_proxy("anything", [])["supported"] is None


def test_abstention_outcomes():
    assert s.abstention_outcome(True, True) == "correct_abstain"
    assert s.abstention_outcome(True, False) == "missed_abstain"     # hallucination risk
    assert s.abstention_outcome(False, True) == "false_abstain"      # over-refusal
    assert s.abstention_outcome(False, False) == "answered"


def _run_all() -> int:
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"  PASS {fn.__name__}")
    print(f"\n{len(fns)}/{len(fns)} QA-scorer tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
