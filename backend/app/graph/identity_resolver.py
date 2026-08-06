"""Populate the durable `entity_identity` table from per-document entity mentions.

Graph step 3. DERIVED + idempotent: rebuilds an owner's identities from the
current `Entity` rows by clustering (graph/resolve), so it always matches the
live graph and survives any single document's re-extraction. Cheap — a user has
at most a few hundred person/org mentions.
"""
from __future__ import annotations

import re

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.graph import resolve
from app.orm import Document, Entity, EntityIdentity

_PERSIST_KINDS = ("person", "org")
# Names that are extraction noise, not real entities: bare PII placeholders
# ("[PERSON_1] [PERSON_3]") or a lone identifier token ("S6862320J").
_NOISE_TOKENS = re.compile(r"^(\s*\[[A-Z0-9_]+\]\s*)+$")


def _is_noise_name(name: str) -> bool:
    n = (name or "").strip()
    if not n or _NOISE_TOKENS.match(n):
        return True
    return " " not in n and any(c.isdigit() for c in n)  # single ID-like token


def _owner_entity_rows(db: Session, tenant_id: str, owner_pk: int | None) -> list[dict]:
    q = (select(Entity.pk, Entity.kind, Entity.canonical, Entity.text, Entity.document_pk)
         .join(Document, Document.pk == Entity.document_pk)
         .where(Document.tenant_id == tenant_id,
                Document.ingestion_status == "ready",
                Document.is_archived.is_(False),
                Entity.kind.in_(_PERSIST_KINDS)))
    if owner_pk is not None:
        q = q.where(Document.owner_user_id == owner_pk)
    return [{"pk": r.pk, "kind": r.kind, "canonical": r.canonical, "text": r.text,
             "document_pk": r.document_pk} for r in db.execute(q).all()]


def rebuild_for_owner(db: Session, tenant_id: str, owner_pk: int | None) -> int:
    """Recompute the durable identities for one owner from their current mentions.
    Delete-then-reinsert (the set is small), so it self-heals after re-extraction.
    Returns the identity count. Caller commits."""
    rows = _owner_entity_rows(db, tenant_id, owner_pk)
    idents = [i for i in resolve.cluster(rows)
              if i.kind in _PERSIST_KINDS and i.key and not _is_noise_name(i.name)]

    dstmt = delete(EntityIdentity).where(EntityIdentity.tenant_id == tenant_id)
    dstmt = (dstmt.where(EntityIdentity.owner_user_id == owner_pk) if owner_pk is not None
             else dstmt.where(EntityIdentity.owner_user_id.is_(None)))
    db.execute(dstmt)

    for ident in idents:
        aliases = sorted({(m.get("text") or "").strip() for m in ident.members if m.get("text")})
        db.add(EntityIdentity(
            tenant_id=tenant_id, owner_user_id=owner_pk, kind=ident.kind,
            identity_key=ident.key[:256], display_name=(ident.name or ident.key)[:256],
            aliases=aliases[:50], doc_pks=sorted(ident.doc_pks),
            mention_count=len(ident.members),
        ))
    return len(idents)
