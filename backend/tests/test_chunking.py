"""Tests for chunking.py — text normalization, shingling, dedup.

Pure functions — no DB, no LLM."""

import pytest
from app.chunking import normalize_text, _shingles, dedup_indices


class TestNormalizeText:
    def test_nfkc_fullwidth(self):
        # Full-width digits → ASCII
        assert normalize_text("１２３") == "123"

    def test_nfkc_ligature(self):
        # ﬁ (U+FB01) ligature → "fi"
        result = normalize_text("ﬁle")
        assert result == "file"

    def test_preserves_newlines_and_tabs(self):
        assert normalize_text("hello\nworld\t!") == "hello\nworld\t!"

    def test_strips_control_chars(self):
        # Null byte removed
        assert "\x00" not in normalize_text("abc\x00def")

    def test_empty(self):
        assert normalize_text("") == ""
        assert normalize_text(None) is None

    def test_idempotent(self):
        # Already-NFKC text is unchanged
        text = "Hello World 123"
        assert normalize_text(text) == text


class TestShingles:
    def test_basic(self):
        sh = _shingles("the quick brown fox")
        assert "the quick brown" in sh
        assert "quick brown fox" in sh

    def test_too_few_tokens(self):
        sh = _shingles("hello world")
        assert sh == frozenset({"hello world"})

    def test_single_token(self):
        sh = _shingles("hello")
        assert sh == frozenset({"hello"})

    def test_empty(self):
        assert _shingles("") == frozenset()

    def test_case_insensitive(self):
        assert _shingles("Hello World") == _shingles("hello world")


class TestDedupIndices:
    def test_no_duplicates(self):
        texts = ["the quick brown fox", "hello world", "completely different"]
        kept = dedup_indices(texts)
        assert kept == [0, 1, 2]

    def test_exact_duplicate(self):
        texts = ["the quick brown fox jumps", "the quick brown fox jumps"]
        kept = dedup_indices(texts)
        assert kept == [0]  # second is near-identical

    def test_near_duplicate_by_default_threshold(self):
        # Default threshold is 0.9 (near-identical). Two texts with 1 word
        # different in 7 3-grams → Jaccard = 6/8 = 0.75 < 0.9 → both kept.
        texts = [
            "the quick brown fox jumps over the lazy dog",
            "the quick brown fox jumps over the lazy cat",
        ]
        kept = dedup_indices(texts)
        assert kept == [0, 1]

    def test_near_duplicate_lower_threshold(self):
        # At threshold 0.7, the same texts DO get deduped (6/8 = 0.75 ≥ 0.7)
        texts = [
            "the quick brown fox jumps over the lazy dog",
            "the quick brown fox jumps over the lazy cat",
        ]
        kept = dedup_indices(texts, threshold=0.7)
        assert kept == [0]

    def test_dissimilar_texts_kept(self):
        texts = [
            "the quick brown fox",
            "hello world foo bar",
            "something else entirely different here",
        ]
        kept = dedup_indices(texts)
        assert len(kept) == 3

    def test_empty_list(self):
        assert dedup_indices([]) == []

    def test_single_item(self):
        assert dedup_indices(["hello"]) == [0]

    def test_custom_threshold(self):
        texts = ["a b c d e", "a b c d f"]
        # At threshold 0.5 they share most 3-grams → dup
        assert len(dedup_indices(texts, threshold=0.5)) == 1
        # At threshold 0.99 they need to be nearly identical
        assert len(dedup_indices(texts, threshold=0.99)) == 2
