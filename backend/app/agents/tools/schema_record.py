"""Tool · schema_record · the document's values as a typed record in its schema's shape.

Complements get_extracted_field: returns EVERY schema field with its value — including fields
derived from the universal envelope (parties/amounts/dates/identifiers) — so the agent can answer
structured questions even when a value lives under a generic envelope key rather than a flat field.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

NAME = "schema_record"
DESCRIPTION = (
    "The document's values as a typed record in its schema's shape — every schema field with its "
    "value (or omitted if missing), including fields derived from the universal envelope. Use for "
    "structured questions ('list the invoice fields', 'what is the tax / grand total') or when "
    "get_extracted_field misses because the value is under a generic key."
)
PARAMS_SCHEMA = {"type": "object", "properties": {}, "required": []}


def call(*, db: Session, tenant_id: str, doc_id: str, **_: object) -> dict:
    from app.orm import Document
    from app.services import schema_json

    doc = db.scalar(select(Document).where(
        Document.tenant_id == tenant_id, Document.id_external == doc_id))
    if doc is None:
        return {"found": False, "error": "doc not found"}
    out = schema_json.schema_shaped(db, doc)
    rec = {k: v for k, v in (out.get("record") or {}).items() if v not in (None, "", [], {})}
    return {
        "found": True,
        "schema": out.get("schemaLabel"),
        "source": out.get("schemaSource"),
        "fields": rec,
        "missing": (out.get("conformance") or {}).get("missing", [])[:12],
    }
