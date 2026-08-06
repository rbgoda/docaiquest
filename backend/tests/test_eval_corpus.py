"""Golden eval corpus — capture gating + record shaping (no DB).

The privacy-critical property: `capture_case` is a no-op unless the document is
training-eligible (consented free) AND actually has an extraction. The full
capture→export→coverage DB path is exercised end-to-end at deploy on throwaway pg.
"""
from __future__ import annotations

import types

from app.services import eval_corpus as ec


class _Doc:
    def __init__(self, ef, owner=1):
        self.extracted_fields = ef
        self.owner_user_id = owner
        self.tenant_id = "t"
        self.pk = 7
        self.id_external = "doc-abc"


def test_capture_noop_without_extraction():
    # No extracted_fields → False, before any eligibility/DB work.
    assert ec.capture_case(None, _Doc(None)) is False
    assert ec.capture_case(None, _Doc({})) is False
    assert ec.capture_case(None, _Doc({"doc_type": "x"})) is False   # no `fields`


def test_capture_noop_when_ineligible(monkeypatch):
    # Has an extraction, but the owner isn't a consented free user → dropped.
    monkeypatch.setattr(ec, "is_training_eligible", lambda db, o: False)
    doc = _Doc({"doc_type": "invoice", "fields": {"total": "10"}})
    assert ec.capture_case(None, doc) is False   # never touches the DB


def test_to_record_shape():
    row = types.SimpleNamespace(
        doc_type="invoice", detected_doc_type="commercial_invoice", trust_score=0.94,
        verified=True, edit_count=2, fields={"total": "10"}, field_confidence={"total": 0.9})
    r = ec._to_record(row)
    assert r == {
        "docType": "invoice", "detectedDocType": "commercial_invoice",
        "trustScore": 0.94, "verified": True, "editCount": 2,
        "fields": {"total": "10"}, "fieldConfidence": {"total": 0.9},
    }
