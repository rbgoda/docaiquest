"""Offline tests for per-field extraction confidence (Reducto-parity G4).

Pure-stdlib: `python -m pytest` OR `python backend/tests/test_field_confidence.py`.
"""
from __future__ import annotations

from app import field_confidence as fc


def test_empty_value_is_omitted():
    # Empty/N-A fields are omitted (not scored 0.0) so they don't drag the mean or
    # flag as errors — but real fields still score.
    s = fc.score_fields({"invoice_no": "", "x": None, "total": "USD 5.00"})
    assert "invoice_no" not in s and "x" not in s and "total" in s


def test_grounded_field_beats_ungrounded():
    fields = {"name": "Kalyani Goda"}
    grounded = fc.score_fields(fields, field_bboxes={"name": {"page": 1}}, doc_confidence=0.7)
    plain = fc.score_fields(fields, field_bboxes={}, doc_confidence=0.7)
    assert grounded["name"] > plain["name"]


def test_well_formed_date_boosts_mismatch_penalized():
    good = fc.score_fields({"issue_date": "2026-03-14"}, doc_confidence=0.7)["issue_date"]
    bad = fc.score_fields({"issue_date": "not a date at all"}, doc_confidence=0.7)["issue_date"]
    assert good > bad
    assert bad <= 0.55  # clearly-wrong date value flagged for review


def test_money_field_needs_a_number():
    ok = fc.score_fields({"total_due": "USD 12,420.00"}, doc_confidence=0.7)["total_due"]
    bad = fc.score_fields({"total_due": "pending"}, doc_confidence=0.7)["total_due"]
    assert ok > bad


def test_email_validation():
    ok = fc.score_fields({"contact_email": "a@b.com"}, doc_confidence=0.7)["contact_email"]
    bad = fc.score_fields({"contact_email": "not-an-email"}, doc_confidence=0.7)["contact_email"]
    assert ok > bad and bad < fc.LOW_CONFIDENCE


def test_composite_fields_use_prior():
    s = fc.score_fields({"parties": [{"name": "A"}], "empty_list": []}, doc_confidence=0.9)
    assert s["parties"] == 0.9
    assert "empty_list" not in s  # empty composite omitted, not 0.0


def test_prune_empty_cleans_fields_and_confidence():
    # The resume case: generic-envelope fields that don't apply come back empty.
    ef = {
        "doc_type": "resume",
        "fields": {"title": "Resume of X", "primary_amount": "", "issuer_address": "",
                   "records": [{"kind": "experience"}], "tags": []},
        "field_confidence": {"title": 0.95, "primary_amount": 0.0, "issuer_address": 0.0,
                             "records": 0.95, "tags": 0.0},
    }
    out = fc.prune_empty(ef)
    assert set(out["fields"]) == {"title", "records"}
    assert set(out["field_confidence"]) == {"title", "records"}
    # mean of the pruned confidences is high (no 0.0 drag)
    vals = list(out["field_confidence"].values())
    assert sum(vals) / len(vals) >= 0.9
    # original untouched (non-destructive)
    assert "primary_amount" in ef["fields"]


def test_doc_confidence_is_the_prior():
    hi = fc.score_fields({"ref": "ABC123"}, doc_confidence=0.95)["ref"]
    lo = fc.score_fields({"ref": "ABC123"}, doc_confidence=0.3)["ref"]
    assert hi > lo


def test_low_confidence_fields_queue():
    scores = {"a": 0.9, "b": 0.4, "c": 0.55, "d": 0.6}
    assert set(fc.low_confidence_fields(scores)) == {"b", "c"}  # < 0.6


def test_review_queue_excludes_missing_by_default():
    # 0.0 == nothing extracted (empty optional field) → not a review item.
    scores = {"total": 0.9, "subtype": 0.0, "bad_date": 0.5}
    assert fc.low_confidence_fields(scores) == ["bad_date"]  # missing excluded
    assert set(fc.low_confidence_fields(scores, include_missing=True)) == {"subtype", "bad_date"}


def test_scores_bounded():
    s = fc.score_fields(
        {"issue_date": "2026-01-01", "total": "$5.00", "email": "x@y.io", "blob": {"k": 1}},
        field_bboxes={"issue_date": {}, "total": {}}, doc_confidence=1.0,
    )
    assert all(0.0 <= v <= 1.0 for v in s.values())


def _run_all() -> int:
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"  PASS {fn.__name__}")
    print(f"\n{len(fns)}/{len(fns)} field-confidence tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
