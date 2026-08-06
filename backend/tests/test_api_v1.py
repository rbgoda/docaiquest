"""Unit tests for v1 partner API helpers — group-access authorization + the
evidence projection. No DB/LLM (the full flow is covered by the PR's live smoke).
"""
from __future__ import annotations

from types import SimpleNamespace

from app.api_clients import Caller
from app.routers.api_v1 import _doc_evidence


def test_legacy_key_may_access_any_group():
    c = Caller(pk=None, name="legacy", scopes=["*"], legacy=True)
    assert c.may_access_group(1) is True
    assert c.may_access_group(999) is True


def test_partner_key_only_granted_groups():
    c = Caller(pk=5, name="AuditAIQ", scopes=["audit:match"], allowed_group_ids=[4, 7])
    assert c.may_access_group(4) is True
    assert c.may_access_group(7) is True
    assert c.may_access_group(8) is False
    assert c.may_access_group(999) is False


def test_partner_key_with_no_grants_accesses_nothing():
    c = Caller(pk=6, name="ungranted", scopes=["audit:match"], allowed_group_ids=[])
    assert c.may_access_group(4) is False


def test_doc_evidence_projects_fields_and_citations():
    doc = SimpleNamespace(
        id_external="doc-1", name="passport.pdf", doc_type="passport",
        doc_type_confidence=0.9,
        extracted_fields={"confidence": 0.95,
                          "fields": {"document_number": "E123", "is_expired": False},
                          "field_bboxes": {"document_number": {"page": 1}}},
    )
    ev = _doc_evidence(doc)
    assert ev["docId"] == "doc-1" and ev["docType"] == "passport"
    assert ev["confidence"] == 0.95
    assert ev["fields"]["document_number"] == "E123"
    assert ev["citations"]["document_number"]["page"] == 1
