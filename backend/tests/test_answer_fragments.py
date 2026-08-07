"""#3 · answer-fragment router — pure routing tests + the 1088-question sweep.

Zero LLM/DB deps (services/answer_fragments is pure regex). Locks in the router
that decides which shape rules each cross-doc chat answer gets.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.answer_fragments import (
    BASE_RULES, FRAGMENTS, build_rules_block, expected_format, select_answer_fragments,
)

_FRAG_KEYS = set(FRAGMENTS.keys())
_SHAPES = {"table", "filtered-list", "one-line", "attribute", "free"}


def test_base_rules_always_present_and_greeting_is_free():
    block, picks = build_rules_block("hello there")
    assert block.startswith("RULES:")
    for r in BASE_RULES:
        assert r in block
    assert picks == []                       # a greeting selects no shape fragment
    assert expected_format("hello there") == "free"


def test_compare_shape_excludes_single():
    assert "compare" in select_answer_fragments("Compare my two resumes")
    assert "compare" in select_answer_fragments("what is the total across all my invoices")
    # the guard: a comparison must NOT also pick 'single'
    assert "single" not in select_answer_fragments("Compare my two resumes")
    assert expected_format("Compare my two resumes") == "table"


def test_single_value_shape():
    assert "single" in select_answer_fragments("What is the total on the invoice?")
    assert "single" in select_answer_fragments("What is the balance in my bank statement?")
    assert expected_format("What is the balance?") == "one-line"


def test_attribute_shape():
    assert "attribute" in select_answer_fragments("Who is the applicant?")
    assert "attribute" in select_answer_fragments("Whose name is on the passport?")


def test_of_kind_shape():
    assert "of_kind" in select_answer_fragments("Which documents are national IDs?")
    assert "of_kind" in select_answer_fragments("List all my invoices")


def test_picks_always_subset_of_fragments():
    for q in ["compare X and Y", "what is the total", "who is the owner",
              "list my invoices", "summarize this document", ""]:
        assert set(select_answer_fragments(q)) <= _FRAG_KEYS
        _, picks = build_rules_block(q)
        assert set(picks) <= _FRAG_KEYS


def test_1088_question_sweep():
    """Every real question routes without raising, picks ⊆ fragments, shape is known."""
    pytest.skip("qa/qa_data.json removed from OSS repo")
