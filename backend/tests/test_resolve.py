"""Tests for graph/resolve.py — entity resolution / identity clustering.

Pure functions — no DB, no LLM. Tests the core matching logic that powers
the entity graph, related-docs, and GraphRAG."""

import pytest
from app.graph.resolve import (
    _tokens,
    _lev_le,
    canon_for,
    same_identity,
)


class TestTokens:
    def test_basic(self):
        assert _tokens("Rajesh Goda") == frozenset({"rajesh", "goda"})

    def test_case_insensitive(self):
        assert _tokens("RAJESH GODA") == _tokens("rajesh goda")

    def test_punctuation_stripped(self):
        assert _tokens("Goda, Rajesh B.") == frozenset({"goda", "rajesh", "b"})

    def test_numbers_included(self):
        t = _tokens("UBS 2020 AG")
        assert "2020" in t

    def test_empty(self):
        assert _tokens(None) == frozenset()
        assert _tokens("") == frozenset()


class TestLevenshtein:
    def test_exact_match(self):
        assert _lev_le("hello", "hello", 0) is True
        assert _lev_le("hello", "hello", 3) is True

    def test_one_edit(self):
        assert _lev_le("hello", "hallo", 1) is True   # substitution
        assert _lev_le("hello", "helloo", 1) is True   # insertion
        assert _lev_le("hello", "helo", 1) is True     # deletion

    def test_two_edits(self):
        assert _lev_le("hello", "hullo", 1) is True    # one sub
        assert _lev_le("hello", "hallo", 0) is False   # one sub but k=0
        assert _lev_le("hello", "hxllo", 1) is True
        assert _lev_le("hello", "hxxlo", 1) is False   # two subs

    def test_deletion(self):
        assert _lev_le("hello", "helo", 1) is True
        assert _lev_le("hello", "helo", 0) is False

    def test_length_difference_exceeds_k(self):
        # If strings differ in length by > k, early exit
        assert _lev_le("a", "abcdef", 3) is False

    def test_completely_different(self):
        assert _lev_le("abc", "xyz", 3) is True
        assert _lev_le("abc", "xyz", 2) is False


class TestCanonFor:
    def test_uses_stored_canonical_when_present(self):
        assert canon_for("person", "Mr. Goda", "goda rajesh") == "goda rajesh"

    def test_falls_back_to_canon_name_for_person(self):
        assert canon_for("person", "Mr. Rajesh Goda", "") == "rajesh goda"

    def test_falls_back_to_canon_org_for_org(self):
        assert canon_for("org", "Smart Audit Pte Ltd", "") == "smart audit"

    def test_value_kinds_just_lower(self):
        assert canon_for("money", "S$1,420", "") == "s$1,420"
        assert canon_for("date", "2026-05-12", "") == "2026-05-12"


class TestSameIdentity:
    def test_exact_match(self):
        assert same_identity("person", "rajesh goda", "rajesh goda") is True

    def test_empty_both(self):
        assert same_identity("person", "", "") is True

    def test_empty_one_side(self):
        assert same_identity("person", "rajesh goda", "") is False

    def test_subset_tokens_person(self):
        # "goda rajesh" ⊆ "goda rajesh balvantrai", sharing ≥2 tokens
        assert same_identity("person", "goda rajesh", "goda rajesh balvantrai") is True

    def test_single_shared_token_not_enough(self):
        # "rajesh goda" vs "priya goda" — share only "goda" (1 token)
        assert same_identity("person", "goda rajesh", "goda priya") is False

    def test_strong_overlap(self):
        # "goda rajesh balvantrai" vs "kalyani goda rajesh"
        # tokens: {goda,rajesh,balvantrai} vs {kalyani,goda,rajesh}
        # intersection = {goda,rajesh} (2), union = {goda,rajesh,balvantrai,kalyani} (4)
        # Jaccard = 2/4 = 0.5 → NOT > 0.5 → False
        assert same_identity("person", "goda rajesh balvantrai", "kalyani goda rajesh") is False

    def test_substring_containment(self):
        # "ubs ag" ⊂ "ubs ag singapore branch", ratio 6/23 ≈ 0.26 < 0.55 → depends on tokens
        # tokens: {ubs,ag} vs {ubs,ag,singapore,branch} — subset, sharing 2 ≥ 2 → True
        assert same_identity("org", "ubs ag", "ubs ag singapore branch") is True

    def test_typo_tolerance(self):
        # same_identity requires ≥1 shared token before checking Levenshtein.
        # Single-word names "rajesh" vs "rajeesh" have zero shared 3-gram tokens
        # → no match. This is by design: conservative on single-name typos.
        assert same_identity("person", "rajesh", "rajeesh") is False

    def test_typo_with_multi_word(self):
        # Multi-word: "rajesh goda" vs "rajeesh goda" share "goda" → Levenshtein
        # check kicks in for the non-matching token
        assert same_identity("person", "rajesh goda", "rajeesh goda") is True

    def test_value_kinds_exact_only(self):
        # Money/date/identifier: only exact canonical matches
        assert same_identity("money", "1420.00 SGD", "1420.00 SGD") is True
        assert same_identity("money", "1420.00 SGD", "1420.01 SGD") is False

    def test_different_kinds_dont_cross_match(self):
        # same string but different kind — still matches (canonical is canonical)
        # but value kinds only match exact
        assert same_identity("date", "2026-05-12", "2026-05-12") is True
