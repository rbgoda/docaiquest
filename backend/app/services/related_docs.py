"""Related documents + duplicate detection for the 'Linked' tab.

- DUPLICATES: exact copies are already blocked at upload (documents.sha256 unique per owner), so
  here we catch NEAR-copies with a different name/format/scan: matching identifiers (same
  invoice_number, etc.) or the same envelope (issuer + primary_amount + primary_date).
- RELATED: documents that share a graph entity (same person/org, via the reconciled canonical).

Owner-scoped throughout. Cheap: SQL + in-Python compare, no LLM, no embedding math (v1).
"""
from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.documents_scope import get_current_owner_user_pk
from app.orm import Document, Entity


def _scope_owner(doc: Document) -> int | None:
    """Owner to scope related/duplicate results to. Use the CALLER's owner (not the
    doc's), so opening a group-SHARED doc never enumerates the doc owner's OTHER
    private documents. Falls back to the doc's owner only in no-owner (admin/audit)
    contexts. When the caller IS the owner (the normal 'Linked' tab), identical."""
    return get_current_owner_user_pk() or doc.owner_user_id


def _norm(s) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


def _fields(doc: Document) -> dict:
    ef = doc.extracted_fields if isinstance(doc.extracted_fields, dict) else {}
    return ef.get("fields") if isinstance(ef.get("fields"), dict) else {}


# Doc-SPECIFIC identifiers only (a duplicate signal). Deliberately EXCLUDES the generic
# identifiers[] array + person/entity IDs (NRIC, UEN, passport, account holder) — those recur
# across many of a person's documents, so they mean "related", not "duplicate".
_DUP_ID_KEYS = ("invoice_number", "receipt_id", "statement_id", "policy_number",
                "order_number", "confirmation_number", "reference", "reference_number")


def _doc_identifiers(f: dict) -> set[str]:
    out = set()
    for k in _DUP_ID_KEYS:
        v = _norm(f.get(k))
        if len(v) >= 4:
            out.add(v)
    return out


def find_duplicates(db: Session, doc: Document, *, limit: int = 8) -> list[dict]:
    tid, owner = doc.tenant_id, _scope_owner(doc)
    out: list[dict] = []
    seen: set[str] = set()

    # 1. Exact — identical content hash (rare; upload usually blocks it, but a purge/re-add can).
    if doc.sha256:
        for d in db.scalars(select(Document).where(
                Document.tenant_id == tid, Document.owner_user_id == owner,
                Document.sha256 == doc.sha256, Document.pk != doc.pk)):
            out.append({"id": d.id_external, "name": d.name, "confidence": 1.0,
                        "reason": "identical file (same content hash)"})
            seen.add(d.id_external)

    f = _fields(doc)
    my_ids = _doc_identifiers(f)
    my_env = (_norm(f.get("issuer")), _norm(f.get("primary_amount")), _norm(f.get("primary_date")))
    env_ok = all(my_env)

    if not my_ids and not env_ok:
        return out[:limit]

    # Near-copy candidates are SAME doc_type only — a shared number across different types is
    # coincidence (or a person ID), not a duplicate.
    others = db.scalars(select(Document).where(
        Document.tenant_id == tid, Document.owner_user_id == owner,
        Document.pk != doc.pk, Document.ingestion_status == "ready",
        Document.doc_type == doc.doc_type)).all()
    for o in others:
        if o.id_external in seen:
            continue
        of = _fields(o)
        common = my_ids & _doc_identifiers(of)
        if common:
            out.append({"id": o.id_external, "name": o.name, "confidence": 0.92,
                        "reason": f"same {doc.doc_type} number ({sorted(common)[0]})"})
            seen.add(o.id_external)
            continue
        if env_ok and (_norm(of.get("issuer")), _norm(of.get("primary_amount")),
                       _norm(of.get("primary_date"))) == my_env:
            out.append({"id": o.id_external, "name": o.name, "confidence": 0.82,
                        "reason": "same issuer + amount + date"})
            seen.add(o.id_external)
    out.sort(key=lambda x: -x["confidence"])
    return out[:limit]


def find_related(db: Session, doc: Document, *, limit: int = 10) -> list[dict]:
    """Documents sharing a graph entity (same person/org via the reconciled canonical)."""
    my_canon = {c for c in db.scalars(select(Entity.canonical).where(
        Entity.document_pk == doc.pk, Entity.canonical.isnot(None),
        Entity.kind.in_(("person", "org")))).all() if c}
    if not my_canon:
        return []
    scope_owner = _scope_owner(doc)
    rows = db.execute(select(Entity.document_pk, Entity.text, Entity.canonical).where(
        Entity.tenant_id == doc.tenant_id, Entity.canonical.in_(my_canon),
        Entity.kind.in_(("person", "org")), Entity.document_pk != doc.pk)).all()
    by_doc: dict[int, set] = {}
    for pk, text, canon in rows:
        by_doc.setdefault(pk, set()).add(text or canon)
    out: list[dict] = []
    for pk, shared in sorted(by_doc.items(), key=lambda x: -len(x[1])):
        d = db.get(Document, pk)
        if d is not None and d.owner_user_id == scope_owner:
            out.append({"id": d.id_external, "name": d.name, "shared": sorted(shared)[:3]})
        if len(out) >= limit:
            break
    return out
