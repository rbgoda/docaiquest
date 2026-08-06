"""Flag-gated doc-type canonicalization (converges free-form LLM slugs onto the
canonical DOC_TYPES). Off = today's behavior byte-for-byte."""
import json

from app.agents import classifier
from app.agents.classifier import (DOC_TYPES, _DOC_TYPE_ALIASES,
                                    canonicalize_doc_type)


def test_canonicalize_maps_known_variants():
    assert canonicalize_doc_type("medical_lab_report") == "lab_report"
    assert canonicalize_doc_type("laboratory_test_report") == "lab_report"
    assert canonicalize_doc_type("blood_test") == "lab_report"
    assert canonicalize_doc_type("cc_statement") == "credit_card_statement"
    assert canonicalize_doc_type("driving_license") == "driver_licence"


def test_canonicalize_passthrough_and_openvocab():
    assert canonicalize_doc_type("lab_report") == "lab_report"      # already canonical
    assert canonicalize_doc_type("a_brand_new_kind_of_doc") is None  # open-vocab preserved
    assert canonicalize_doc_type("") is None
    assert canonicalize_doc_type(None) is None
    assert canonicalize_doc_type("  Medical_Lab_Report ") == "lab_report"  # normalized


def test_all_alias_values_are_canonical():
    unknown = {v for v in _DOC_TYPE_ALIASES.values() if v not in DOC_TYPES}
    assert not unknown, f"alias targets not in DOC_TYPES: {unknown}"
    # keys must not already be canonical (those pass through, no alias needed)
    dupes = {k for k in _DOC_TYPE_ALIASES if k in DOC_TYPES}
    assert not dupes, f"alias keys that are already canonical: {dupes}"


def _payload(doc_type):
    return {"choices": [{"message": {"content": json.dumps(
        {"guesses": [{"doc_type": doc_type, "confidence": 0.9, "evidence": "e"}]})}}]}


def test_classifier_coercion_is_flag_gated(monkeypatch):
    class _S:
        type_canonicalize = False
    monkeypatch.setattr(classifier, "get_settings", lambda: _S())
    # OFF → unknown slug coerced to 'other' (unchanged behavior)
    assert classifier._parse_response(_payload("medical_lab_report")).top.doc_type == "other"

    class _S2:
        type_canonicalize = True
    monkeypatch.setattr(classifier, "get_settings", lambda: _S2())
    # ON → canonicalized to 'lab_report'
    assert classifier._parse_response(_payload("medical_lab_report")).top.doc_type == "lab_report"
    # ON but genuinely-new → still 'other' (no false mapping)
    assert classifier._parse_response(_payload("zzz_unknown_type")).top.doc_type == "other"
    # a canonical type is always accepted regardless of flag
    assert classifier._parse_response(_payload("invoice")).top.doc_type == "invoice"
