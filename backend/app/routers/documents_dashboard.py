"""M46 · Documents System · per-user home dashboard stats.

Documents-product only (404 elsewhere), owner-scoped: every figure is computed
over the current user's own documents/chunks/chats. Kept in its own
documents-owned router so the documents footprint on shared code stays small.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_current_tenant, get_session
from app.documents_scope import get_current_owner_user_pk
from app.orm import ChatMessage, ConnectorAccount, Document, DocumentChunk, LearnedSchema
from app.security import CurrentUser, get_current_user

router = APIRouter()


def _guard() -> None:
    if get_settings().product != "documents":
        raise HTTPException(status_code=404, detail="Not available")


# NOTE path is `/documents-dashboard` not `/documents/dashboard` — the latter
# would be captured by the documents router's `/documents/{doc_id}` route.
@router.get("/documents-dashboard")
def dashboard(db: Session = Depends(get_session),
              user: CurrentUser = Depends(get_current_user)) -> dict:
    _guard()
    tid = get_current_tenant()
    uid = get_current_owner_user_pk()

    docs = list(db.scalars(
        select(Document).where(
            Document.tenant_id == tid,
            Document.owner_user_id == uid,
            Document.is_archived.is_(False),
        ).order_by(Document.pk.desc())
    ).all())
    doc_pks = [d.pk for d in docs]
    doc_ids = [d.id_external for d in docs]

    by_status: dict[str, int] = {}
    by_type: dict[str, int] = {}
    by_source: dict[str, int] = {}
    pages = 0
    records_extracted = 0
    conf_sum = 0.0
    conf_n = 0
    pii_count = 0
    for d in docs:
        st = d.ingestion_status or "pending"
        by_status[st] = by_status.get(st, 0) + 1
        pages += d.pages or 0
        by_source[d.source or "upload"] = by_source.get(d.source or "upload", 0) + 1
        if d.pii_protected:
            pii_count += 1
        if d.doc_type_confidence is not None:
            conf_sum += float(d.doc_type_confidence)
            conf_n += 1
        ef = d.extracted_fields or {}
        f = ef.get("fields") if isinstance(ef.get("fields"), dict) else ef
        if isinstance(f, dict):
            records_extracted += len(f.get("records") or [])
        if d.ingestion_status == "ready":
            t = (d.doc_type or "unclassified")
            by_type[t] = by_type.get(t, 0) + 1

    total_chunks = 0
    indexed_bytes = 0
    if doc_pks:
        total_chunks = db.scalar(
            select(func.count()).select_from(DocumentChunk)
            .where(DocumentChunk.tenant_id == tid, DocumentChunk.document_pk.in_(doc_pks))
        ) or 0
        indexed_bytes = db.scalar(
            select(func.coalesce(func.sum(func.length(DocumentChunk.text)), 0))
            .where(DocumentChunk.tenant_id == tid, DocumentChunk.document_pk.in_(doc_pks))
        ) or 0

    # Questions = user-authored chat turns over this user's docs (per-doc chat)
    # or their cross-doc workspace thread (`user:<pk>`).
    cond = ChatMessage.workspace_key == f"user:{uid}"
    if doc_ids:
        cond = cond | ChatMessage.doc_id_external.in_(doc_ids)
    questions = db.scalar(
        select(func.count()).select_from(ChatMessage)
        .where(ChatMessage.tenant_id == tid, ChatMessage.role == "user", cond)
    ) or 0

    drive = db.scalar(
        select(func.count()).select_from(ConnectorAccount)
        .where(ConnectorAccount.tenant_id == tid,
               ConnectorAccount.owner_user_id == uid,
               ConnectorAccount.provider == "drive")
    ) or 0

    # What the workspace has LEARNED (PR-U2) — surfaced so the dashboard shows
    # the self-learning extractor maturing per doc-type.
    learned = db.scalars(
        select(LearnedSchema).where(LearnedSchema.tenant_id == tid)
        .order_by(LearnedSchema.seen_count.desc()).limit(8)
    ).all()
    learned_out = [{
        "docType": ls.doc_type, "seenCount": ls.seen_count,
        "fieldCount": len(ls.fields or {}),
        "recordKinds": list((ls.record_kinds or {}).keys()),
    } for ls in learned]

    recent = [{
        "id": d.id_external, "name": d.name,
        "status": d.ingestion_status, "docType": d.doc_type,
        "source": d.source, "modified": d.modified,
    } for d in docs[:8]]

    return {
        "totalDocs": len(docs),
        "readyDocs": by_status.get("ready", 0),
        "byStatus": by_status,
        "byType": [{"type": k, "count": v}
                   for k, v in sorted(by_type.items(), key=lambda kv: -kv[1])],
        "bySource": by_source,
        "totalPages": pages,
        "totalChunks": int(total_chunks),
        "indexedBytes": int(indexed_bytes),
        "recordsExtracted": records_extracted,
        "avgConfidence": round(conf_sum / conf_n, 2) if conf_n else None,
        "piiProtected": pii_count,
        "questionsAsked": int(questions),
        "driveConnected": drive > 0,
        "learnedSchemas": learned_out,
        "recent": recent,
    }
