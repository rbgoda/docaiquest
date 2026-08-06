"""Move-1 PR1 · precise clustering + label canonicalization for the self-learning
extractor. Pure-logic coverage (no DB): the label canonicalizer, the detected-type
slug, and the distinctive-label extractor. The DB-touching re-key / centroid fold
wraps already-tested repos and is exercised end-to-end at ingest.
"""
from __future__ import annotations

import pytest


# ── learned_schemas.canon_label ────────────────────────────────────────────
@pytest.mark.parametrize("raw,expected", [
    ("Interest Rate", "interest_rate"),
    ("interest_rate", "interest_rate"),
    ("rate", "interest_rate"),          # alias fold
    ("Int Rate", "interest_rate"),      # "int rate" → int_rate → alias
    ("The Account Number", "account_number"),   # article stripped
    ("Acct No", "account_number"),      # alias
    ("account_no", "account_number"),   # alias
    ("DOB", "date_of_birth"),           # alias
    ("Loan Term (months)", "loan_term_months"),
    ("  Coverage   Limit  ", "coverage_limit"),
])
def test_canon_label(raw, expected):
    from app.repositories.learned_schemas import canon_label
    assert canon_label(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", "___", None])
def test_canon_label_empty_is_none(raw):
    from app.repositories.learned_schemas import canon_label
    assert canon_label(raw) is None


# ── fact_extractor._slug_type ──────────────────────────────────────────────
@pytest.mark.parametrize("raw,expected", [
    ("Mortgage Statement", "mortgage_statement"),
    ("mortgage_statement", "mortgage_statement"),
    ("Form 1099-DIV", "form_1099_div"),
    ("Brokerage Portfolio Statement", "brokerage_portfolio_statement"),
])
def test_slug_type(raw, expected):
    from app.agents.fact_extractor import _slug_type
    assert _slug_type(raw) == expected


@pytest.mark.parametrize("raw", ["other", "unknown", "document", "misc", "general", "", None])
def test_slug_type_weak_is_none(raw):
    # Weak/empty labels must NOT become a cluster key (falls back to classifier).
    from app.agents.fact_extractor import _slug_type
    assert _slug_type(raw) is None


# ── fact_extractor._learned_labels ─────────────────────────────────────────
def test_learned_labels_pulls_only_distinctive_array_labels():
    from app.agents.fact_extractor import _learned_labels
    args = {
        # universal base scalars — must be IGNORED (no clustering signal).
        "detected_doc_type": "mortgage_statement",
        "title": "Monthly Statement",
        "issuer": "Acme Bank",
        # distinctive labeled arrays — the learned vocabulary.
        "key_facts": [
            {"label": "Interest Rate", "value": "6.25%"},
            {"label": "Loan Term Months", "value": "360"},
            {"not_a_label": "x"},            # skipped (no label)
            "bogus",                          # skipped (not a dict)
        ],
        "identifiers": [{"label": "Account Number", "value": "123"}],
        "dates": [{"label": "Statement Date", "value": "2026-06-01"}],
        "amounts": [{"label": "Total Due", "value": "USD 1,250"}],
        "records": [{"kind": "transaction"}],  # kinds handled separately, not here
    }
    labels = _learned_labels(args)
    assert labels == [
        "Interest Rate", "Loan Term Months",   # key_facts
        "Account Number",                        # identifiers
        "Statement Date",                        # dates
        "Total Due",                             # amounts
    ]
    # None of the base scalars leaked in.
    assert "title" not in labels and "issuer" not in labels


def test_learned_labels_empty_args():
    from app.agents.fact_extractor import _learned_labels
    assert _learned_labels({}) == []


# ── PR3b · crystallized-schema adoption merge ──────────────────────────────
def test_augment_schema_fields_adds_without_shadowing():
    from app.agents.fact_extractor import _augment_schema_fields
    base = {"title": {"type": "string"}, "key_facts": {"type": "array"}}
    promoted = {
        "interest_rate": {"type": "string", "description": "d"},
        "title": {"type": "string", "description": "MUST NOT overwrite base"},
        "bad": "not-a-dict",   # skipped
        "": {"type": "string"},  # empty label skipped
    }
    merged = _augment_schema_fields(base, promoted)
    assert merged["interest_rate"]["description"] == "d"      # added
    assert merged["title"] == {"type": "string"}             # base NOT shadowed
    assert "bad" not in merged and "" not in merged          # bad specs dropped
    assert len(merged) == len(base) + 1


def test_augment_schema_fields_none_promoted_is_copy():
    from app.agents.fact_extractor import _augment_schema_fields
    base = {"title": {"type": "string"}}
    merged = _augment_schema_fields(base, None)
    assert merged == base and merged is not base
