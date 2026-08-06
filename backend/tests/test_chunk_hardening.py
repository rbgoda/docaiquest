"""Offline tests for R6 — NFKC normalize + near-duplicate chunk dedup."""
from __future__ import annotations

from app import chunking as ck


def test_nfkc_folds_fullwidth_and_compat():
    # Full-width digits/letters fold to ASCII; ligature expands.
    assert ck.normalize_text("１２３ＡＢＣ") == "123ABC"
    assert ck.normalize_text("ﬁle") == "file"


def test_nfkc_keeps_newlines_strips_control():
    out = ck.normalize_text("line1\nline2\tend\x00\x07")
    assert "\n" in out and "\t" in out
    assert "\x00" not in out and "\x07" not in out


def test_nfkc_empty():
    assert ck.normalize_text("") == ""


def test_dedup_drops_near_identical_keeps_first():
    a = "This document confirms general liability insurance coverage one million dollars."
    a_dup = a + ""           # identical
    b = "Invoice number EA07 total amount due four thousand eighty dollars payable."
    keep = ck.dedup_indices([a, a_dup, b], threshold=0.9)
    assert keep == [0, 2]    # the identical copy at index 1 dropped, first kept


def test_dedup_keeps_distinct():
    texts = [
        "Alpha beta gamma delta epsilon zeta eta theta content one.",
        "Completely different lambda mu nu xi omicron pi rho sigma content two.",
    ]
    assert ck.dedup_indices(texts, threshold=0.9) == [0, 1]


def test_dedup_threshold_sensitivity():
    base = "the quick brown fox jumps over the lazy dog near the river bank today"
    near = base + " and then ran away"   # mostly-overlapping
    # high threshold keeps both; very low threshold collapses them
    assert ck.dedup_indices([base, near], threshold=0.95) == [0, 1]
    assert ck.dedup_indices([base, near], threshold=0.3) == [0]


def _run_all() -> int:
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"  PASS {fn.__name__}")
    print(f"\n{len(fns)}/{len(fns)} chunk-hardening tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
