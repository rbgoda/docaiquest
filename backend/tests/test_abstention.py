"""Offline tests for R1 calibrated chat abstention."""
from __future__ import annotations

from app import abstention as ab


def test_refusal_message_carries_sentinel():
    m = ab.refusal_message(n_docs=3)
    assert m.startswith(ab.INSUFFICIENT)
    assert "3 document" in m
    assert ab.is_abstention(m) and ab.is_abstention("  " + m)
    assert not ab.is_abstention("Here is your answer.")


def test_min_hits_floor():
    # zero hits → abstain; >= min_hits → answer
    assert ab.assess_evidence([], min_hits=1)[0] is True
    assert ab.assess_evidence([0.1], min_hits=1)[0] is False
    assert ab.assess_evidence([0.1, 0.2], min_hits=3)[0] is True


def test_score_floor_off_by_default():
    # min_top_score=None → never abstain on score (only on hit count)
    assert ab.assess_evidence([0.001, 0.002], min_hits=1, min_top_score=None)[0] is False


def test_score_floor_when_calibrated():
    assert ab.assess_evidence([0.2, 0.1], min_hits=1, min_top_score=0.5)[0] is True   # top 0.2 < 0.5
    assert ab.assess_evidence([0.9, 0.1], min_hits=1, min_top_score=0.5)[0] is False  # top 0.9 >= 0.5
    # None entries (no numeric score) → score floor can't fire
    assert ab.assess_evidence([None, None], min_hits=1, min_top_score=0.5)[0] is False


def test_abstain_after_guardrail_is_opt_in():
    assert ab.abstain_after_guardrail(grounded=False, hard=True) is True
    assert ab.abstain_after_guardrail(grounded=False, hard=False) is False  # default = soft caveat
    assert ab.abstain_after_guardrail(grounded=True, hard=True) is False


def _run_all() -> int:
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"  PASS {fn.__name__}")
    print(f"\n{len(fns)}/{len(fns)} abstention tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(_run_all())


# Grounding-gate (chat_abstain_on_ungrounded) contract
def test_abstain_after_guardrail_strict_refuses_ungrounded():
    from app.abstention import abstain_after_guardrail
    assert abstain_after_guardrail(False, hard=True) is True    # ungrounded + strict → refuse
    assert abstain_after_guardrail(True, hard=True) is False    # grounded → answer
    assert abstain_after_guardrail(False, hard=False) is False  # soft mode → caveat, not refuse
    assert abstain_after_guardrail(True, hard=False) is False
