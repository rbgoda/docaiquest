"""Move-1 PR2 · the universal graph-bootstrap handler. Without it, universal-
extracted docs (the DEFAULT extraction path) produced only a bare Document node.
Covered without a DB via a fake ctx that records the entity/link calls the handler
makes, plus the pure person-vs-org + date-relation heuristics.
"""
from __future__ import annotations

import pytest

from app.graph import bootstrap as B


# ── pure heuristics ────────────────────────────────────────────────────────
@pytest.mark.parametrize("name,is_org", [
    ("ACME PTE LTD", True),
    ("Acme Bank", True),          # " BANK" token
    ("Globex Corporation", True), # "CORP" token (upper contains " CORP")
    ("ACME", True),               # ALL-CAPS
    ("Jane Doe", False),
    ("john smith", False),
    ("", False),
])
def test_looks_like_org(name, is_org):
    assert B._looks_like_org(name) is is_org


@pytest.mark.parametrize("label,rel", [
    ("expiry_date", B.REL_EXPIRES_ON),
    ("valid_until", B.REL_EXPIRES_ON),
    ("policy_maturity", B.REL_EXPIRES_ON),
    ("effective_date", B.REL_EFFECTIVE_ON),
    ("issue_date", B.REL_EFFECTIVE_ON),
    ("statement_date", B.REL_DATED),
    ("", B.REL_DATED),
])
def test_universal_date_rel(label, rel):
    assert B._universal_date_rel(label) == rel


# ── handler via a fake ctx (no DB) ─────────────────────────────────────────
class _FakeEnt:
    def __init__(self, kind, text, **kw):
        self.kind = kind
        self.text = text
        self.kw = kw


class _FakeCtx:
    """Captures the entities + links the handler creates. doc_node is a real
    transient Entity so the handler's metadata-enrich (flag_modified) works."""
    def __init__(self):
        from app.orm import Entity
        self._doc = Entity(kind="document", text="DOC")
        self._doc.entity_metadata = {}
        self.entities = []
        self.links = []

    def doc_node(self):
        return self._doc

    def _mk(self, kind, text, **kw):
        e = _FakeEnt(kind, text, **kw)
        self.entities.append(e)
        return e

    def org(self, name, *, page=1, extra=None):
        return self._mk("org", name, extra=extra) if name else None

    def person(self, name, *, page=1, extra=None):
        return self._mk("person", name, extra=extra) if name else None

    def date(self, raw, *, page=1, role=None):
        return self._mk("date", raw, role=role) if raw else None

    def money(self, raw, *, page=1, extra=None):
        return self._mk("money", raw) if raw else None

    def identifier(self, raw, *, kind_tag, page=1):
        return self._mk("identifier", raw, kind_tag=kind_tag) if raw else None

    def link(self, src, rel, dst, *, confidence=None, metadata=None, page=None):
        if src is None or dst is None:
            return
        self.links.append((
            getattr(src, "kind", None), rel, getattr(dst, "kind", None),
            getattr(src, "text", None), getattr(dst, "text", None), metadata,
        ))


_UNIVERSAL_FIELDS = {
    "detected_doc_type": "insurance_certificate",
    "detected_doc_subtype": "motor",
    "issuer": "Acme Insurance Pte Ltd",
    "issuer_address": "1 Market St",
    "subject_or_recipient": "Jane Doe",
    "parties": [
        {"name": "Globex Corporation", "role": "insurer"},
        {"name": "John Smith", "role": "agent"},
        {"role": "no name"},          # skipped
        "bogus",                       # skipped
    ],
    "primary_date": "2026-01-01",
    "dates": [
        {"label": "expiry_date", "value": "2027-01-01"},
        {"label": "effective_date", "value": "2026-01-01"},
        {"label": "", "value": ""},   # skipped
    ],
    "primary_amount": "USD 1,000.00",
    "amounts": [{"label": "premium", "value": "USD 250.00"}],
    "identifiers": [
        {"label": "policy_number", "value": "POL-123"},
        {"label": "vin", "value": ""},  # skipped
    ],
}


def test_handle_universal_builds_expected_graph():
    ctx = _FakeCtx()
    B._handle_universal(ctx, _UNIVERSAL_FIELDS)

    rels = [(l[0], l[1], l[2]) for l in ctx.links]

    # Issuer → org, ISSUED_BY (doc → org)
    assert ("document", B.REL_ISSUED_BY, "org") in rels
    # Subject "Jane Doe" → person, PARTY_OF (person → doc)
    assert ("person", B.REL_PARTY_OF, "document") in rels
    # Parties: Globex → org, John Smith → person; both PARTY_OF
    party_texts = {l[3] for l in ctx.links if l[1] == B.REL_PARTY_OF}
    assert {"Jane Doe", "Globex Corporation", "John Smith"} <= party_texts
    assert next(e for e in ctx.entities if e.text == "Globex Corporation").kind == "org"
    assert next(e for e in ctx.entities if e.text == "John Smith").kind == "person"

    # Dates: primary (DATED) + expiry (EXPIRES_ON) + effective (EFFECTIVE_ON)
    assert ("document", B.REL_DATED, "date") in rels          # primary_date
    assert ("document", B.REL_EXPIRES_ON, "date") in rels
    assert ("document", B.REL_EFFECTIVE_ON, "date") in rels

    # Amounts: primary + premium → money, HAS_TOTAL
    money_links = [l for l in ctx.links if l[1] == B.REL_HAS_TOTAL and l[2] == "money"]
    assert len(money_links) == 2

    # Identifier: policy_number kept, empty vin dropped
    id_links = [l for l in ctx.links if l[1] == "has_identifier"]
    assert len(id_links) == 1 and id_links[0][4] == "POL-123"

    # Hub enriched with the precise detected type.
    assert ctx._doc.entity_metadata["detected_doc_type"] == "insurance_certificate"
    assert ctx._doc.entity_metadata["detected_doc_subtype"] == "motor"


def test_handle_universal_empty_fields_only_doc_node():
    ctx = _FakeCtx()
    B._handle_universal(ctx, {})
    assert ctx.links == []              # nothing to link
    assert ctx._doc.entity_metadata == {}  # no detected type → no enrich


def test_registered_in_handlers():
    assert B._HANDLERS.get("universal") is B._handle_universal
