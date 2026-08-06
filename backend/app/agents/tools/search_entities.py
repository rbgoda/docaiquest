"""Tool · search_entities · query the graph layer for matching entities.

Useful when the agent needs to find an entity by kind (identifier / money /
date / person / org) and optionally a regex pattern. Scoped to the current
document by default; pass `tenant_wide=true` to search across the tenant
(rare — use cross_doc_search for that).
"""
from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

NAME = "search_entities"
DESCRIPTION = (
    "Find entities by kind (e.g. 'identifier', 'money', 'date', 'person') and "
    "optional regex pattern. Returns matches with text, kind, page, chunk_pk. "
    "Faster than search_chunks for ID-format questions."
)
PARAMS_SCHEMA = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "description": "Entity kind (identifier|money|date|person|org|email|percent)."},
        "pattern": {"type": "string", "description": "Optional regex to filter entity text. Leave empty to return all of this kind."},
        "limit": {"type": "integer", "default": 12},
    },
    "required": ["kind"],
}


def call(*, db: Session, tenant_id: str, doc_id: str, kind: str, pattern: str = "", limit: int = 12, **_: object) -> dict:
    from app.orm import Document, Entity

    doc = db.scalar(
        select(Document).where(
            Document.tenant_id == tenant_id,
            Document.id_external == doc_id,
        )
    )
    if doc is None:
        return {"found": False, "error": "doc not found", "matches": []}

    stmt = (
        select(Entity)
        .where(
            Entity.tenant_id == tenant_id,
            Entity.document_pk == doc.pk,
            Entity.kind == kind,
        )
        .limit(int(limit or 12) * 4)  # widen so regex can filter
    )
    rows = db.scalars(stmt).all()

    rx = None
    if pattern:
        # The pattern is LLM-supplied. Guard against catastrophic-backtracking
        # ReDoS: reject over-long patterns, nested quantifiers ((a+)+ / (a*)* /
        # (.*)+ …), and huge bounded repeats ({1000,}) before compiling.
        if len(pattern) > 128:
            return {"found": False, "error": "pattern too long", "matches": []}
        if re.search(r"\([^)]*[*+][^)]*\)\s*[*+?]|\{\d{3,},?\d*\}", pattern):
            return {"found": False, "error": "pattern too complex", "matches": []}
        try:
            rx = re.compile(pattern)
        except re.error as e:
            return {"found": False, "error": f"bad regex: {e}", "matches": []}

    matches = []
    for r in rows:
        text = (r.text or "").strip()
        # cap the length any single search runs against — bounds worst-case regex work
        if rx and not rx.search(text[:512]):
            continue
        matches.append({
            "text": text,
            "kind": r.kind,
            "page": int(r.page),
            "chunk_pk": int(r.chunk_pk) if r.chunk_pk else None,
        })
        if len(matches) >= int(limit or 12):
            break

    return {"found": bool(matches), "count": len(matches), "matches": matches}
