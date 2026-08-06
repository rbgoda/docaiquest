"""Offline unit tests for OCR/page quality heuristics (Reducto-parity G3 scorer).

Pure-stdlib: `python -m pytest` OR `python backend/tests/test_ocr_quality.py`.
"""
from __future__ import annotations

from app import ocr_quality as oq

CLEAN = (
    "This Certificate of Insurance confirms that the named insured holds general "
    "liability coverage in the amount of one million dollars, effective from "
    "1 January 2026 through 31 December 2026, issued by Acme Mutual."
)


def test_clean_text_scores_high():
    q = oq.page_quality(CLEAN)
    assert q.score >= 0.8
    assert q.flags == []
    assert not oq.is_low_confidence(CLEAN)


def test_empty_is_zero():
    q = oq.page_quality("")
    assert q.score == 0.0 and "empty" in q.flags
    assert oq.page_quality("   \n\t ").score == 0.0


def test_near_empty_flagged():
    q = oq.page_quality("a b")
    assert q.score <= 0.2 and "near_empty" in q.flags


def test_symbol_garbage_low_score():
    garbage = "@#$%^&*()_+{}|<>?~`@#$%^&*()_+{}|<>?~`@#$%^&*()_+{}|<>?~`"
    q = oq.page_quality(garbage)
    assert q.score < 0.55
    assert "low_alpha" in q.flags
    assert oq.is_low_confidence(garbage)


def test_replacement_chars_flagged():
    bad = "Inv�ice t�tal d�e: �1,2�0.00 paid �n full �ank tr�nsfer ref"
    q = oq.page_quality(bad)
    assert "replacement_chars" in q.flags
    assert q.score < 0.9


def test_run_on_tokens_flagged():
    runon = " ".join(["x" * 60 for _ in range(8)])  # 8 huge tokens, no real spacing
    q = oq.page_quality(runon)
    assert "run_on_tokens" in q.flags
    assert q.score < 0.9


def test_score_is_bounded():
    for s in ["", "a", CLEAN, "###", "�" * 200, "word " * 500]:
        q = oq.page_quality(s)
        assert 0.0 <= q.score <= 1.0


def _run_all() -> int:
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"  PASS {fn.__name__}")
    print(f"\n{len(fns)}/{len(fns)} ocr-quality tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
