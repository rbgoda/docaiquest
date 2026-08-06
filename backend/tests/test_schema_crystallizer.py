"""Move-1 PR3a · schema crystallizer core-selection logic (no DB).

The DB driver (`crystallize_tenant`) + nightly job wrap these pure functions; the
risk lives in WHICH labels get promoted, which is fully covered here.
"""
from __future__ import annotations

import pytest

from app.services.schema_crystallizer import (
    build_typed_fields, select_core_fields, _MAX_FIELDS,
)


def test_core_fields_keeps_frequent_drops_rare():
    fields = {"interest_rate": 5, "loan_term": 4, "one_off_note": 1}
    # seen_count 5, ratio 0.5 → threshold = 2 (round(2.5)); one_off (1) drops.
    core = select_core_fields(fields, 5, ratio=0.5)
    assert core == ["interest_rate", "loan_term"]   # frequency-sorted


def test_core_fields_excludes_universal_base_keys():
    fields = {"title": 9, "issuer": 9, "primary_amount": 9, "coverage_limit": 9}
    core = select_core_fields(fields, 9, ratio=0.5)
    assert core == ["coverage_limit"]   # base scalars never promoted


@pytest.mark.parametrize("seen,ratio,expected", [
    (1, 0.5, ["a", "b"]),     # threshold max(1, ceil(0.5))=1 → both qualify
    (4, 0.75, ["a"]),         # threshold ceil(3.0)=3 → only a(4)
    (5, 0.5, ["a"]),          # threshold ceil(2.5)=3 → a(4)≥3, b(2)<3
    (0, 0.5, []),             # no docs → nothing
])
def test_core_fields_threshold(seen, ratio, expected):
    fields = {"a": 4, "b": 2}
    assert select_core_fields(fields, seen, ratio=ratio) == expected


def test_core_fields_empty_and_bad_input():
    assert select_core_fields({}, 5, ratio=0.5) == []
    assert select_core_fields(None, 5, ratio=0.5) == []
    # non-numeric counts are ignored, not crashed on
    assert select_core_fields({"x": "bogus", "y": 5}, 5, ratio=0.5) == ["y"]


def test_core_fields_capped():
    fields = {f"f{i}": 100 for i in range(_MAX_FIELDS + 20)}
    assert len(select_core_fields(fields, 100, ratio=0.5)) == _MAX_FIELDS


def test_build_typed_fields_shape():
    typed = build_typed_fields(["interest_rate", "account_number"])
    assert set(typed) == {"interest_rate", "account_number"}
    assert typed["interest_rate"]["type"] == "string"     # no examples → string
    assert "interest rate" in typed["interest_rate"]["description"]


# ── Move-1 (b) · value-bearing type/enum inference ─────────────────────────
def test_build_typed_fields_infers_number_and_date():
    fex = {
        "interest_rate": {"types": {"number": 5}, "values": ["6.25", "5.9", "7.1"]},
        "statement_date": {"types": {"date": 4}, "values": ["2026-06-01"]},
    }
    typed = build_typed_fields(["interest_rate", "statement_date"], fex)
    assert typed["interest_rate"]["type"] == "number"
    assert typed["statement_date"]["type"] == "string"    # date → string + hint
    assert "date" in typed["statement_date"]["description"]


def test_build_typed_fields_infers_enum():
    fex = {"status": {"types": {"string": 9}, "values": ["active", "closed", "pending"]}}
    typed = build_typed_fields(["status"], fex)
    assert typed["status"]["type"] == "string"
    assert typed["status"]["enum"] == ["active", "closed", "pending"]


def test_build_typed_fields_low_signal_falls_back_to_string():
    fex = {"x": {"types": {"number": 1}, "values": ["7"]}}   # total < 3
    assert build_typed_fields(["x"], fex)["x"]["type"] == "string"
    assert "enum" not in build_typed_fields(["x"], fex)["x"]


@pytest.mark.parametrize("val,expected", [
    ("1,250.00", "number"),
    ("45%", "number"),
    ("USD 1,250.00", "money"),
    ("$99", "money"),
    ("2026-05-12", "date"),
    ("12 May 2026", "date"),
    ("Yes", "boolean"),
    ("false", "boolean"),
    ("Acme Pte Ltd", "string"),
    ("", "string"),
])
def test_classify_value(val, expected):
    from app.repositories.learned_schemas import classify_value
    assert classify_value(val) == expected
