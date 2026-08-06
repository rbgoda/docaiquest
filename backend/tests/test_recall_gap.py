"""Offline tests for the recall-gap detector."""
from __future__ import annotations

from app.services.recall_gap import collect_covered_values, find_gaps


def test_surfaces_uncovered_spans_and_skips_covered():
    text = ("Invoice total USD 4,080.00 due 12 Jul 2026. Contact billing a@b.com. "
            "Ref MH0220090183263. Interest 12.5%.")
    covered = ["USD 4,080.00"]  # total already extracted
    gaps = find_gaps(text, covered)
    vals = " | ".join(g["value"] for g in gaps)
    kinds = {g["kind"] for g in gaps}
    assert "a@b.com" in vals                      # email surfaced
    assert any("2026" in g["value"] for g in gaps)  # date surfaced
    assert "percent" in kinds                      # 9% surfaced
    assert not any("4,080" in g["value"] for g in gaps)  # covered money NOT surfaced


def test_id_and_phone_noise_filtered():
    # short/no-digit tokens must not qualify as ids; short digit runs not phones
    gaps = find_gaps("Section ABC and code A1 near 12 34", [])
    assert not any(g["kind"] == "id" and g["value"] in ("ABC", "A1") for g in gaps)


def test_collect_covered_values_flattens_nested():
    ef = {"fields": {"total": "USD 5.00", "records": [{"amount": "$9", "kind": "line"}],
                     "tags": ["x"], "n": 42}}
    cov = collect_covered_values(ef)
    assert "USD 5.00" in cov and "$9" in cov and "42" in cov
