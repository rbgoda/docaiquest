"""Offline tests for the unified document trust score."""
from __future__ import annotations

from app import doc_trust as dt


def _trust(**kw):
    base = dict(ingestion_status="ready", doc_type="invoice", doc_type_confidence=0.95,
               ocr_quality=None, extracted_fields=None)
    base.update(kw)
    return dt.document_trust(**base)


def test_failed_and_pending_are_low():
    assert _trust(ingestion_status="failed")["level"] == "low"
    assert _trust(ingestion_status="pending")["level"] == "low"


def test_clean_doc_is_high():
    t = _trust()
    assert t["level"] == "high" and t["score"] >= 0.8 and t["reasons"] == []


def test_unclassified_drops_trust():
    t = _trust(doc_type="unclassified", doc_type_confidence=0.5)
    assert "unclassified" in t["reasons"] and t["score"] < 0.8


def test_parsed_but_no_fields_is_unstructured_not_low_accuracy():
    # The Deed case: parsed cleanly (ready, no OCR flags) but unclassified → no fields.
    # Must be framed `unstructured`, NOT presented as an accuracy failure.
    t = _trust(doc_type=None, doc_type_confidence=None, extracted_fields=None)
    assert t["state"] == "unstructured"
    # still surfaces for attention (needs a type), but the FRAMING is not accuracy
    assert dt.needs_review(t) is True
    # and a doc WITH fields is never 'unstructured'
    withfields = _trust(extracted_fields={"fields": {"total": "10.00"}, "field_confidence": {"total": 0.9}})
    assert withfields["state"] == "trusted"


def test_state_maps_to_level_for_structured_docs():
    ef = {"fields": {"a": "x"}, "field_confidence": {"a": 0.9, "b": 0.3, "c": 0.2}}  # 2 uncertain
    low = _trust(ocr_quality={"flagged": True, "lowConfidencePages": 5}, extracted_fields=ef)
    assert low["state"] == "needs_review" and low["level"] == "low"
    high = _trust(extracted_fields={"fields": {"a": "x"}, "field_confidence": {"a": 0.95}})
    assert high["state"] == "trusted"


def test_low_ocr_reduces_and_explains():
    t = _trust(ocr_quality={"flagged": True, "lowConfidencePages": 3})
    assert any(r.startswith("low_ocr_confidence") for r in t["reasons"])
    assert t["score"] < 0.95


def test_uncertain_fields_reduce():
    ef = {"field_confidence": {"total": 0.9, "date": 0.4, "ref": 0.3}}  # 2 uncertain
    t = _trust(extracted_fields=ef)
    assert any(r.startswith("uncertain_fields") for r in t["reasons"])
    # missing (0.0) fields must NOT count (G4-refine): all-empty → no penalty
    clean = _trust(extracted_fields={"field_confidence": {"a": 0.0, "b": 0.9}})
    assert not any(r.startswith("uncertain_fields") for r in clean["reasons"])


def test_combined_low_trust_needs_review():
    t = _trust(doc_type="unclassified", doc_type_confidence=0.4,
               ocr_quality={"flagged": True, "lowConfidencePages": 4},
               extracted_fields={"field_confidence": {"x": 0.2, "y": 0.3}})
    assert t["level"] == "low" and dt.needs_review(t) is True


def test_score_bounded():
    for ef in [None, {"field_confidence": {"a": 0.1}}]:
        t = _trust(extracted_fields=ef, doc_type_confidence=0.99)
        assert 0.0 <= t["score"] <= 1.0


def _run_all() -> int:
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"  PASS {fn.__name__}")
    print(f"\n{len(fns)}/{len(fns)} doc-trust tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(_run_all())


def test_reviewed_doc_is_verified():
    # Human sign-off overrides the model's estimate → verified, not the 90/75 numbers.
    t = _trust(review_status="reviewed", doc_type_confidence=0.9,
               extracted_fields={"fields": {"a": "x"}, "field_confidence": {"a": 0.5, "b": 0.4}})
    assert t["state"] == "verified" and t["level"] == "high" and t["score"] == 1.0
