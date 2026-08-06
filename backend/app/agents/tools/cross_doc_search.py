"""Tool · cross_doc_search · search across other documents in the tenant.

Useful when the agent needs corroborating or contradicting evidence outside
the current document — e.g. "does this passport name match the Aadhaar
on file?" The agent decides when to reach for this; default scope is
the current doc only via search_chunks.

Returns hits grouped by doc, capped to keep observation size sane.
"""
from __future__ import annotations

from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

NAME = "cross_doc_search"
DESCRIPTION = (
    "Search across ALL documents in the tenant (not just the current one). "
    "Use sparingly — only when the answer requires evidence from other docs. "
    "Returns top hits grouped by doc."
)
PARAMS_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {"type": "string"},
        "top_k": {"type": "integer", "default": 8},
    },
    "required": ["query"],
}


def call(*, db: Session, tenant_id: str, doc_id: str, query: str, top_k: int = 8, **_: object) -> dict:
    from app import retrieval
    from app.orm import Document, DocumentChunk

    # No doc_id_external filter → tenant-wide retrieve
    hits = retrieval.retrieve(db, query, top_k=int(top_k or 8))
    by_doc: dict[int, list[dict]] = defaultdict(list)
    for h in hits:
        ch = db.scalar(select(DocumentChunk).where(DocumentChunk.pk == h.chunk_pk))
        if ch is None:
            continue
        text = " ".join((ch.text or "").split())
        by_doc[ch.document_pk].append({
            "chunk_pk": int(ch.pk),
            "page": int(ch.page),
            "text": text[:500],
        })

    out = []
    for doc_pk, chunks in by_doc.items():
        doc = db.scalar(select(Document).where(Document.pk == doc_pk))
        if doc is None:
            continue
        out.append({
            "doc_id_external": doc.id_external,
            "name": doc.name,
            "doc_type": doc.doc_type,
            "is_current_doc": doc.id_external == doc_id,
            "chunks": chunks[:3],
        })

    return {"count": len(out), "docs": out[:6]}
