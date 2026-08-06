"""#4 · typed_answer._coerce + TypedAnswer.rendered — pure output-contract tests.

The LLM call (generate) needs a provider; _coerce/rendered are pure and guard the
fallback contract (bad/partial JSON must degrade safely, not crash).
"""
from __future__ import annotations

from app.services.typed_answer import TypedAnswer, _coerce


def test_coerce_minimal_defaults():
    t = _coerce({"answer": "hi"})
    assert t is not None
    assert t.answer == "hi" and t.answer_found is True and t.format == "none" and t.caveats == []


def test_coerce_rejects_missing_answer_or_non_dict():
    assert _coerce({"answer_found": True}) is None
    assert _coerce("nope") is None
    assert _coerce(None) is None


def test_coerce_clamps_format_and_caveats():
    t = _coerce({"answer": "x", "format": "weird", "caveats": "one string", "answer_found": False})
    assert t.format == "none"                 # unknown format clamped
    assert t.caveats == ["one string"]        # non-list wrapped
    assert t.answer_found is False
    t2 = _coerce({"answer": "x", "caveats": ["a", "b", "c", "d", "e", "f"]})
    assert len(t2.caveats) == 4               # truncated to 4


def test_rendered_appends_caveats_only_when_present():
    assert TypedAnswer("The total is $5.", True, "single", []).rendered() == "The total is $5."
    r = TypedAnswer("The total is $5.", True, "single", ["may be pre-tax"]).rendered()
    assert "The total is $5." in r and "may be pre-tax" in r and "_Note:" in r
