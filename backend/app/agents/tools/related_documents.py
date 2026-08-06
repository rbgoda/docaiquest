"""Tool · related_documents · other docs connected to this one via the entity graph + duplicates.

Distinct from cross_doc_search (keyword search across docs): this returns documents that share a
reconciled person/org entity, plus near-duplicate copies (same identifier, or same issuer+amount+
date). Use for "what other documents relate to this?", "is this a duplicate?", or to pull the set
of documents about the same party.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

NAME = "related_documents"
DESCRIPTION = (
    "Other documents connected to this one via the entity graph (same person/org) and near-"
    "duplicates (same identifier, or same issuer+amount+date). Use for 'what other documents "
    "relate to this?', 'is this a duplicate?', or to gather all documents about the same party."
)
PARAMS_SCHEMA = {"type": "object", "properties": {}, "required": []}


def call(*, db: Session, tenant_id: str, doc_id: str, **_: object) -> dict:
    from app.orm import Document
    from app.services import related_docs

    doc = db.scalar(select(Document).where(
        Document.tenant_id == tenant_id, Document.id_external == doc_id))
    if doc is None:
        return {"found": False, "error": "doc not found"}
    related = related_docs.find_related(db, doc, limit=6)
    duplicates = related_docs.find_duplicates(db, doc, limit=4)
    return {
        "found": True,
        "related": [{"name": r["name"], "shared": r.get("shared", [])} for r in related],
        "duplicates": [{"name": d["name"], "why": d["reason"]} for d in duplicates],
    }
