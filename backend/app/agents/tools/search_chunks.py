"""Tool · search_chunks · hybrid BM25+cosine retrieval, optionally reranked.

This is the agent's main "look in the document" verb. Wraps the existing
`app.retrieval.retrieve` which already runs BM25 + cosine RRF fusion and
(when enabled) BGE reranker — same path the legacy single-shot uses.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

NAME = "search_chunks"
DESCRIPTION = (
    "Search the document's text using hybrid retrieval. Use when the answer is "
    "in the document body and not in a structured extracted field. "
    "Returns top chunks with text, page, and chunk_pk."
)
PARAMS_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {"type": "string", "description": "The search query."},
        "top_k": {"type": "integer", "default": 6, "description": "How many chunks to return."},
    },
    "required": ["query"],
}


def call(*, db: Session, tenant_id: str, doc_id: str, query: str, top_k: int = 6, **_: object) -> dict:
    from app import retrieval
    from app.orm import DocumentChunk

    hits = retrieval.retrieve(db, query, top_k=int(top_k or 6), doc_id_external=doc_id)
    out = []
    for h in hits:
        ch = db.scalar(select(DocumentChunk).where(DocumentChunk.pk == h.chunk_pk))
        if ch is None:
            continue
        text = " ".join((ch.text or "").split())
        out.append({
            "chunk_pk": int(ch.pk),
            "page": int(ch.page),
            "text": text[:800],
            "score": float(getattr(h, "score", 0.0) or 0.0),
        })
    return {"chunks": out, "count": len(out)}
