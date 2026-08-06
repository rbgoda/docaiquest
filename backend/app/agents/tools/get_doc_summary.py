"""Tool · get_doc_summary · whole-doc orientation.

When the agent isn't sure where to start, this returns the document's
doc_type + the extractor's notes + the intro-chunk text. Useful as a first
step when the question is vague ("what is this document?").
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

NAME = "get_doc_summary"
DESCRIPTION = (
    "Get the document's type, classifier confidence, extractor notes, and the "
    "first page of text. Use when the question is vague or you need orientation "
    "before drilling into specific fields/chunks."
)
PARAMS_SCHEMA = {
    "type": "object",
    "properties": {},
    "required": [],
}


def call(*, db: Session, tenant_id: str, doc_id: str, **_: object) -> dict:
    from app.orm import Document, DocumentChunk

    doc = db.scalar(
        select(Document).where(
            Document.tenant_id == tenant_id,
            Document.id_external == doc_id,
        )
    )
    if doc is None:
        return {"found": False, "error": "doc not found"}

    ef = doc.extracted_fields or {}
    notes = ef.get("notes") if isinstance(ef, dict) else None

    intro = db.scalars(
        select(DocumentChunk)
        .where(DocumentChunk.document_pk == doc.pk)
        .order_by(DocumentChunk.chunk_index)
        .limit(2)
    ).all()
    intro_text = "\n".join(" ".join((c.text or "").split())[:600] for c in intro)

    return {
        "found": True,
        "name": doc.name,
        "doc_type": doc.doc_type,
        "doc_type_confidence": doc.doc_type_confidence,
        "pages": doc.pages,
        "notes": (str(notes)[:500] if notes else None),
        "intro_text": intro_text,
    }
