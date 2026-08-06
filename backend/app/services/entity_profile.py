"""Entity intelligence profile — everything known about ONE entity across ALL of
a user's documents. Deterministic, no-LLM, owner-scoped.

Resolves a query name to a unified cross-document identity (graph/resolve), then
aggregates that identity's footprint: the documents it appears in, co-occurring
people/orgs (its network), a date timeline, and the amounts / identifiers /
roles seen alongside it. This is the cross-document intelligence surface the
graph previously lacked (it was only ever queried per-document).
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_current_tenant
from app.documents_scope import get_current_owner_user_pk
from app.graph import resolve
from app.orm import Document, Entity

_RELATED_KINDS = ("person", "org")


def _owner_entity_rows(db: Session) -> list[dict]:
    """Every entity mention across the caller's ready, non-archived documents."""
    tenant = get_current_tenant()
    owner = get_current_owner_user_pk()
    q = (
        select(Entity.pk, Entity.kind, Entity.canonical, Entity.text,
               Entity.document_pk, Entity.page)
        .join(Document, Document.pk == Entity.document_pk)
        .where(Document.tenant_id == tenant,
               Document.ingestion_status.in_(("ready", "failed")),
               Document.is_archived.is_(False))
    )
    if owner is not None:
        q = q.where(Document.owner_user_id == owner)
    return [{"pk": r.pk, "kind": r.kind, "canonical": r.canonical, "text": r.text,
             "document_pk": r.document_pk, "page": r.page} for r in db.execute(q).all()]


def _primary_date(doc: Document) -> str | None:
    ef = doc.extracted_fields or {}
    f = ef.get("fields", {}) if isinstance(ef, dict) else {}
    return f.get("primary_date") or f.get("date_of_issue") or None


def build_profile(db: Session, query: str, *, kind: str | None = None,
                  max_docs: int = 40, max_related: int = 12) -> dict | None:
    """Resolve `query` to an identity and aggregate its cross-document footprint.
    Returns None when no entity in the corpus matches."""
    rows = _owner_entity_rows(db)
    ident = resolve.best_match(rows, query, kind=kind)
    if ident is None:
        return None

    doc_pks = ident.doc_pks
    self_pks = {m["pk"] for m in ident.members}
    co = [r for r in rows if r["document_pk"] in doc_pks and r["pk"] not in self_pks]

    # documents this identity appears in
    docs = db.execute(select(Document).where(Document.pk.in_(doc_pks))).scalars().all()
    doc_by_pk = {d.pk: d for d in docs}
    documents = sorted(
        ({"docId": d.id_external, "name": d.name, "docType": d.doc_type,
          "date": _primary_date(d)} for d in docs),
        key=lambda x: (x["date"] or ""), reverse=True,
    )[:max_docs]

    # network — co-occurring people/orgs, resolved + ranked by shared-doc count
    related_rows = [r for r in co if r["kind"] in _RELATED_KINDS]
    related = sorted(
        ({"name": ri.name, "kind": ri.kind, "sharedDocs": len(ri.doc_pks & doc_pks)}
         for ri in resolve.cluster(related_rows)),
        key=lambda x: x["sharedDocs"], reverse=True,
    )[:max_related]

    def _by_kind(k: str) -> list[dict]:
        seen, out = set(), []
        for r in co:
            if r["kind"] != k:
                continue
            val = (r["canonical"] or r["text"] or "").strip()
            if not val or val.lower() in seen:
                continue
            seen.add(val.lower())
            d = doc_by_pk.get(r["document_pk"])
            out.append({"value": r.get("text") or val, "docName": d.name if d else None})
        return out

    # date timeline (sorted), amounts, identifiers, roles
    timeline = sorted(_by_kind("date"), key=lambda x: (x["value"] or ""))

    return {
        "name": ident.name,
        "kind": ident.kind,
        "aliases": sorted({(m.get("text") or "").strip() for m in ident.members if m.get("text")}),
        "docCount": len(doc_pks),
        "documents": documents,
        "related": related,             # the entity's network
        "timeline": timeline,           # dates seen alongside it
        "amounts": _by_kind("money"),
        "identifiers": _by_kind("identifier"),
        "roles": _by_kind("role"),
    }
