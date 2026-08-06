"""Offline tests for R2 chain-of-verification (the pure parser + rollups).

The LLM `verify()` call is exercised live; here we test the deterministic pieces:
claim splitting, verdict parsing (incl. fail-open), and the rollups.
"""
from __future__ import annotations

from app.agents import claim_verifier as cv


def test_split_claims_drops_caveat():
    ans = "The total is 12420. It is due Nov 1.\n_⚠ verify against the source._"
    claims = cv.split_claims(ans)
    assert claims == ["The total is 12420.", "It is due Nov 1."]


def test_parse_verdicts_basic():
    text = "1: SUPPORTED\n2: UNSUPPORTED — not in the evidence\n3: SUPPORTED"
    v = cv.parse_verdicts(text, 3)
    assert v[0] == (True, "")
    assert v[1][0] is False and "evidence" in v[1][1]
    assert v[2] == (True, "")


def test_parse_verdicts_fail_open_on_missing():
    # only claim 2 reported → 1 and 3 default to supported (never block)
    v = cv.parse_verdicts("2: UNSUPPORTED — nope", 3)
    assert v[0] == (True, "") and v[2] == (True, "")
    assert v[1][0] is False


def test_parse_verdicts_tolerates_formats():
    v = cv.parse_verdicts("[1] - UNSUPPORTED: bad\n2) SUPPORTED", 2)
    assert v[0][0] is False
    assert v[1][0] is True


def test_summarize_and_drop():
    verified = [
        {"claim": "A is true.", "supported": True, "reason": ""},
        {"claim": "B is fabricated.", "supported": False, "reason": "no support"},
    ]
    s = cv.summarize(verified)
    assert s["n"] == 2 and s["unsupported"] == 1 and s["all_supported"] is False
    assert s["flags"][0]["claim"] == "B is fabricated."
    assert cv.drop_unsupported(verified) == "A is true."


def test_empty():
    assert cv.parse_verdicts("", 0) == []
    assert cv.summarize([])["all_supported"] is True


def _run_all() -> int:
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"  PASS {fn.__name__}")
    print(f"\n{len(fns)}/{len(fns)} claim-verifier tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
