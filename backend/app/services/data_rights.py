"""M46 · §compliance · GDPR/PDPA data-subject rights for the documents product.

- export_user_data: everything the server holds for one user (DSAR / portability,
  Arts 15 & 20).
- erase_user_data: delete it all + the account (right to erasure, Art 17),
  including the reflexion 'general' tier that survives per-doc delete.

Owner scope is set by the request middleware; these run inside the authenticated
user's own context and only ever touch that user's rows.
"""
from __future__ import annotations

import logging

from sqlalchemy import select, text as _sql, tuple_
from sqlalchemy.orm import Session

from app.orm import (ChatFeedback, ChatMessage, ConnectorAccount, Document,
                     DocumentGroup, DocumentGroupMember, Entity, EntityCanonical,
                     LearnedDocType, LLMCall, LLMCallAudit, ReflexionPair)
from app.repositories import documents as doc_repo

log = logging.getLogger("docaiq.data_rights")


def export_user_data(db: Session, *, uid: int, email: str, tenant_id: str) -> dict:
    """Read-only snapshot of everything the server holds for this user."""
    from app import pii_vault
    docs = db.scalars(select(Document).where(Document.owner_user_id == uid)
                      .order_by(Document.pk)).all()
    documents = []
    for d in docs:
        chunks = doc_repo.chunks_for_doc(db, d.pk, tenant_id=tenant_id)
        joined = "\n\n".join((c.text or "") for c in chunks)
        # DSAR (Arts 15/20): the user is entitled to their OWN real values, not the
        # at-rest [CREDIT_CARD_1] placeholders. Detokenize this owner's own doc back
        # to cleartext for the export (owner-scoped, their data only).
        if d.pii_protected:
            try:
                joined = pii_vault.detokenize(db, d.pk, joined)
            except Exception as e:  # noqa: BLE001 — never fail the export on a vault hiccup
                log.warning("export: detokenize failed for doc %s: %s", d.pk, e)
        documents.append({
            "id": d.id_external, "name": d.name, "docType": d.doc_type,
            "uploadedBy": d.uploaded_by, "source": d.source,
            "piiProtected": d.pii_protected,
            "extractedFields": d.extracted_fields,
            "text": joined,
        })
    # Doc chat + cross-doc (workspace) chat for this user.
    doc_ids = [d.id_external for d in docs]
    chat = db.scalars(select(ChatMessage).where(
        (ChatMessage.doc_id_external.in_(doc_ids) if doc_ids else _sql("false")) |
        (ChatMessage.workspace_key == f"user:{uid}")
    ).order_by(ChatMessage.pk)).all()
    learned = db.scalars(select(LearnedDocType).where(
        LearnedDocType.owner_user_id == uid)).all()
    groups = db.scalars(select(DocumentGroup).where(
        DocumentGroup.created_by_user_id == uid)).all()
    feedback = db.scalars(select(ChatFeedback).where(
        ChatFeedback.owner_user_id == uid)).all()
    return {
        "exportedFor": email,
        "documents": documents,
        "chatMessages": [{"role": m.role, "text": m.text, "docId": m.doc_id_external,
                          "workspace": m.workspace_key} for m in chat],
        "learnedTypes": [{"slug": t.type_slug, "label": t.label, "source": t.source,
                          "seenCount": t.seen_count} for t in learned],
        "groupsOwned": [{"name": g.name, "createdBy": g.created_by_email} for g in groups],
        "chatFeedback": [{"direction": f.direction, "feedback": f.feedback,
                          "category": f.category, "suggestion": f.suggestion} for f in feedback],
        "note": ("Original files live in your Google Drive. This export covers the "
                 "data DocAIQ holds server-side. Reflexion/cache rows are internal "
                 "learning artifacts and are deleted, not exported."),
    }


def erase_user_data(db: Session, *, uid: int, email: str, tenant_id: str) -> dict:
    """Hard-delete every trace of this user + the account. Returns row counts.
    Order matters: child/non-FK rows first, then documents (FK CASCADE handles
    chunks/entities/artifacts/pii_vault/field_edits/highlights/diffs/shares),
    then the user row, then the object-storage blobs (post-commit).

    `entity_canonical` (reconciled person/org names) is tenant-global with no owner
    link, so it's deleted query-scoped: only canonicals referenced SOLELY by this
    user's docs (cross-checked against other users' Entity rows) are removed — a name
    another user still has survives."""
    counts: dict[str, int] = {}
    email_l = (email or "").lower()
    # Collect doc identity (pk / id / storage key) + owned group ids BEFORE any
    # deletion — needed for the non-cascade rows (llm_calls by document_pk, group
    # chat threads) and the object-storage blobs that no DB cascade can reach.
    doc_rows = db.execute(select(Document.pk, Document.id_external, Document.s3_key)
                          .where(Document.owner_user_id == uid)).all()
    doc_ids = [r.id_external for r in doc_rows]
    doc_pks = [r.pk for r in doc_rows]
    s3_keys = [r.s3_key for r in doc_rows if r.s3_key]
    owned_group_ids = list(db.scalars(select(DocumentGroup.pk).where(
        DocumentGroup.created_by_user_id == uid)).all())

    # 1. Chat — doc threads + the user's cross-doc workspace thread (no FK to docs).
    if doc_ids:
        counts["chatMessages"] = db.query(ChatMessage).filter(
            ChatMessage.doc_id_external.in_(doc_ids)).delete(synchronize_session=False)
    counts["workspaceChat"] = db.query(ChatMessage).filter(
        ChatMessage.workspace_key == f"user:{uid}").delete(synchronize_session=False)
    # Group cross-doc threads (workspace_key='group:{gid}') for groups the user owns —
    # the groups themselves are deleted in step 4, so their chat must go too (chat has
    # no FK to document_groups).
    if owned_group_ids:
        counts["groupChat"] = db.query(ChatMessage).filter(
            ChatMessage.workspace_key.in_([f"group:{g}" for g in owned_group_ids])
        ).delete(synchronize_session=False)

    # 1b. LLM audit + per-doc cost rows — neither is FK-cascaded off documents.
    counts["llmAudit"] = db.query(LLMCallAudit).filter(
        LLMCallAudit.user_email == email_l).delete(synchronize_session=False)
    if doc_pks:
        counts["llmCalls"] = db.query(LLMCall).filter(
            LLMCall.document_pk.in_(doc_pks)).delete(synchronize_session=False)

    # 2. Reflexion — incl. the 'general' (doc_id NULL) tier that survives per-doc delete.
    counts["reflexionPairs"] = db.query(ReflexionPair).filter(
        ReflexionPair.owner_user_id == uid).delete(synchronize_session=False)

    # 3. Learned vocabulary, chat feedback, connector tokens.
    counts["learnedTypes"] = db.query(LearnedDocType).filter(
        LearnedDocType.owner_user_id == uid).delete(synchronize_session=False)
    counts["chatFeedback"] = db.query(ChatFeedback).filter(
        ChatFeedback.owner_user_id == uid).delete(synchronize_session=False)
    counts["connectorAccounts"] = db.query(ConnectorAccount).filter(
        ConnectorAccount.owner_user_id == uid).delete(synchronize_session=False)

    # 4. Groups the user OWNS → delete (CASCADE removes members/events/shares).
    owned = db.scalars(select(DocumentGroup).where(
        DocumentGroup.created_by_user_id == uid)).all()
    for g in owned:
        db.delete(g)
    counts["groupsOwned"] = len(owned)
    # The user's memberships in OTHER people's groups.
    counts["groupMemberships"] = db.query(DocumentGroupMember).filter(
        (DocumentGroupMember.user_id == uid) |
        (DocumentGroupMember.member_email == email_l)).delete(synchronize_session=False)

    # 4b. entity_canonical (reconciled real person/org NAMES) — tenant-global with no
    #     owner/document link, so delete ONLY the canonicals referenced SOLELY by this
    #     user's docs; a name another user's doc also has must survive. Runs BEFORE the
    #     doc delete below, while this user's Entity rows still exist to cross-reference.
    if doc_pks:
        mine = set(db.execute(select(Entity.kind, Entity.canonical).where(
            Entity.document_pk.in_(doc_pks), Entity.canonical.isnot(None))).all())
        if mine:
            shared = set(db.execute(
                select(Entity.kind, Entity.canonical)
                .join(Document, Document.pk == Entity.document_pk)
                .where(Document.owner_user_id != uid,
                       Entity.canonical.in_({c for _, c in mine}))).all())
            solely = list(mine - shared)
            if solely:
                counts["entityCanonical"] = db.query(EntityCanonical).filter(
                    EntityCanonical.tenant_id == tenant_id,
                    tuple_(EntityCanonical.kind, EntityCanonical.canonical).in_(solely)
                ).delete(synchronize_session=False)

    # 5. Documents — FK CASCADE wipes chunks/entities/artifacts/pii_vault/
    #    field_edits/highlights/diffs/document_group_shares keyed on documents.pk.
    counts["documents"] = db.query(Document).filter(
        Document.owner_user_id == uid).delete(synchronize_session=False)

    # 6. The account itself.
    from app.orm import User
    counts["user"] = db.query(User).filter(User.pk == uid).delete(synchronize_session=False)

    db.commit()

    # AFTER the DB erase commits: delete the original file blobs from object storage
    # (MinIO/S3). No DB cascade can reach these — account-erase previously left every
    # uploaded original in the bucket. Done post-commit + best-effort so a storage
    # hiccup leaves at worst a logged orphan blob, never a half-erased database.
    purged = 0
    if s3_keys:
        from app import storage
        for key in s3_keys:
            try:
                storage.delete_object(key)
                purged += 1
            except Exception as e:  # noqa: BLE001
                log.warning("erase_user_data · uid=%s · storage delete failed for %s: %s", uid, key, e)
    counts["storageBlobs"] = purged

    log.info("erase_user_data · uid=%s · %s", uid, counts)
    return counts
