from datetime import datetime, timezone

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app import doc_trust as _doc_trust
from app import field_confidence as _field_conf
from app.db import get_current_tenant, get_current_vendor_pk
from app.documents_scope import get_current_owner_user_pk
from app.orm import (
    AuditRun, AuditRunRequirement, ChatMessage, Diff, Document, DocumentChunk,
    FieldEdit, Highlight, ReflexionPair, Requirement,
)


def _vendor_clause():
    """Returns an additional WHERE clause to scope a query to the current
    vendor (M17 phase 3) — or `True` (a no-op) when the user isn't
    vendor-scoped. Vendor-scoped users only see Documents whose vendor_pk
    matches their own."""
    vpk = get_current_vendor_pk()
    if vpk is None:
        return True
    return Document.vendor_pk == vpk


def _owner_clause():
    """M46 · Documents System per-user isolation. Returns a WHERE clause that
    scopes the query to the current user's own documents PLUS documents shared
    into a group they belong to — or `True` (no-op) when there's no per-user
    owner in context. The owner scope is set by the middleware ONLY in the
    documents product, so in the auditing product this is always a no-op and
    audit behaviour is unchanged."""
    uid = get_current_owner_user_pk()
    if uid is None:
        return True
    if uid <= 0:
        # M46 · §4 · fail-closed deny sentinel (authenticated but no valid
        # owner). Match nothing rather than everything.
        from sqlalchemy import false
        return false()
    from sqlalchemy import or_
    from app.orm import DocumentGroupMember, DocumentGroupShare
    my_groups = (
        select(DocumentGroupMember.group_id)
        .where(DocumentGroupMember.user_id == uid)
    )
    shared_to_me = (
        select(DocumentGroupShare.document_pk)
        .where(DocumentGroupShare.group_id.in_(my_groups))
    )
    return or_(Document.owner_user_id == uid, Document.pk.in_(shared_to_me))


def _group_ids_for(db: Session, doc_pks: list[int]) -> dict[int, list[int]]:
    """Batch-load the group ids each document is shared into (avoids N+1)."""
    if not doc_pks:
        return {}
    from app.orm import DocumentGroupShare
    rows = db.execute(
        select(DocumentGroupShare.document_pk, DocumentGroupShare.group_id)
        .where(DocumentGroupShare.document_pk.in_(doc_pks))
    ).all()
    out: dict[int, list[int]] = {}
    for doc_pk, gid in rows:
        out.setdefault(doc_pk, []).append(gid)
    return out


def _to_dict(row: Document, edit_count: int = 0,
             threshold: float | None = None,
             duplicate_doc_ids: set[str] | None = None,
             group_ids: list[int] | None = None) -> dict:
    d = {
        "id": row.id_external,
        "name": row.name,
        "path": row.path,
        "size": row.size,
        "modified": row.modified,
        # Real upload time (when the doc entered DocAIQ). `modified` is a display string that
        # is only meaningful for connector/Drive docs; direct uploads default it to "just now".
        "uploadedAt": row.created_at.isoformat() if getattr(row, "created_at", None) else None,
        "pages": row.pages,
        "currentPage": row.current_page,
        "type": row.type,
        "content": row.content,
        # M6 additions — null for seeded demo docs.
        "mimeType": row.mime_type,
        "sha256": row.sha256,
        "uploadedBy": row.uploaded_by,
        "hasFile": row.s3_key is not None,
        # M7 ingestion lifecycle.
        "ingestionStatus": row.ingestion_status,
        "ingestionError": row.ingestion_error,
        # KYC extraction (Phase 1). Populated after the matcher
        # auto-attaches the doc to a KYC-* requirement.
        # Prune empty / not-applicable fields for display — an empty generic-envelope
        # field (e.g. primary_amount on a resume) only clutters the schema and drags the
        # confidence mean. Trust below still sees the full stored blob.
        "extractedFields": _field_conf.prune_empty(row.extracted_fields),
        # G3 · OCR page-quality summary (None for non-OCR docs).
        "ocrQuality": row.ocr_quality,
        # Unified trust score (classification + OCR-G3 + field-conf-G4) → review triage.
        "trust": _doc_trust.document_trust(
            ingestion_status=row.ingestion_status, doc_type=row.doc_type,
            doc_type_confidence=row.doc_type_confidence, ocr_quality=row.ocr_quality,
            extracted_fields=row.extracted_fields, review_status=row.review_status,
        ),
        # M51 · user-applied tags (labels).
        "tags": row.tags or [],
        # M11.6 classification (top-3 doc-type guesses from the classifier).
        "docType": row.doc_type,
        "docTypeConfidence": row.doc_type_confidence,
        "docTypeAlternatives": row.doc_type_alternatives,
        # Sub-tenant scope (M17). NULL for tenant-general docs.
        "vendorPk": row.vendor_pk,
        # M46 · connector provenance + retention. source="drive" for connector
        # pulls; retainOriginal=false + hasFile=false means the blob was purged
        # and the original is re-pullable from source on demand.
        "source": row.source,
        "sourceRef": row.source_ref,
        "retainOriginal": row.retain_original,
        # M46 · a doc can be shared into several groups (document_group_shares).
        # groupIds is the live source of truth; groupId kept for back-compat.
        "groupIds": group_ids if group_ids is not None else [],
        "groupId": (group_ids[0] if group_ids else None),
        # M46 · True when the caller owns this doc. Drives owner-only controls
        # (group sharing). None/owner-pk unset (auditing product) → True (no-op).
        "ownedByMe": (get_current_owner_user_pk() in (None, row.owner_user_id)),
        # M44.P11.2 · PII-at-rest. piiProtected = stored text is tokenized;
        # piiRevealed = an authorized user toggled detokenized viewing.
        "piiProtected": row.pii_protected,
        "piiRevealed": row.pii_revealed,
        # Reviewer sign-off (M27). live status + who/when/note. History
        # in document_reviews via /edit-history endpoint.
        "reviewStatus": row.review_status,
        "reviewNote": row.review_note,
        "reviewedBy": row.reviewed_by,
        "reviewedAt": row.reviewed_at.isoformat() if row.reviewed_at else None,
        # M27.1 · count of HITL field overrides recorded against this doc.
        # Drives the Expenses/Income "Accuracy" column — `AI 92% · HITL ×3`.
        "hitlEditCount": edit_count,
        # M29 · soft-archive markers. Frontend uses isArchived to switch
        # the row's action button (Delete → Unarchive) and to grey it out.
        "isArchived": row.is_archived,
        "archivedAt": row.archived_at.isoformat() if row.archived_at else None,
        "archivedBy": row.archived_by,
    }
    # M28 · compute review reasons on-the-fly. Cheap (pure-python over the
    # already-loaded dict). Returns empty list when the doc passes.
    from app.document_review import review_reasons
    d["reviewReasons"] = review_reasons(d, threshold=threshold,
                                        duplicate_doc_ids=duplicate_doc_ids)
    return d


def list_all_map(db: Session, *, include_archived: bool = False,
                 personal_only: bool = False) -> dict[str, dict]:
    from app.config import get_settings
    tid = get_current_tenant()
    where = [Document.tenant_id == tid, _vendor_clause()]
    uid = get_current_owner_user_pk()
    if personal_only and uid is not None:
        # Personal Documents tab — only my own docs that aren't shared into any
        # of my groups. Group docs live under each group's own scope tab.
        from app.orm import DocumentGroupMember, DocumentGroupShare
        my_groups = select(DocumentGroupMember.group_id).where(
            DocumentGroupMember.user_id == uid)
        shared = select(DocumentGroupShare.document_pk).where(
            DocumentGroupShare.group_id.in_(my_groups))
        where.append(Document.owner_user_id == uid)
        where.append(Document.pk.notin_(shared))
    else:
        where.append(_owner_clause())
    if not include_archived:
        where.append(Document.is_archived.is_(False))
    rows = db.scalars(
        select(Document)
        .where(*where)
        .order_by(Document.pk)
        .limit(get_settings().max_list_rows)
    ).all()
    # One grouped query for HITL edit counts — avoids N+1 across the list.
    edit_counts = dict(
        db.execute(
            select(FieldEdit.document_pk, func.count())
            .where(FieldEdit.tenant_id == tid)
            .group_by(FieldEdit.document_pk)
        ).all()
    )
    # Read auto-approve threshold + duplicate set once for the whole list.
    from app.document_review import get_document_threshold, get_duplicate_doc_ids
    threshold = get_document_threshold(db)
    dup_ids = get_duplicate_doc_ids(db)
    gids = _group_ids_for(db, [r.pk for r in rows])
    return {
        r.id_external: _to_dict(r, edit_counts.get(r.pk, 0),
                                threshold=threshold, duplicate_doc_ids=dup_ids,
                                group_ids=gids.get(r.pk, []))
        for r in rows
    }


def get(db: Session, id_external: str) -> dict | None:
    row = get_row(db, id_external)
    if row is None:
        return None
    tid = get_current_tenant()
    cnt = db.scalar(
        select(func.count()).select_from(FieldEdit)
        .where(FieldEdit.tenant_id == tid, FieldEdit.document_pk == row.pk)
    ) or 0
    from app.document_review import get_document_threshold, get_duplicate_doc_ids
    d = _to_dict(row, cnt,
                 threshold=get_document_threshold(db),
                 duplicate_doc_ids=get_duplicate_doc_ids(db),
                 group_ids=_group_ids_for(db, [row.pk]).get(row.pk, []))
    # M44.P11.2 · when the owner has REVEALED this doc, the single-doc detail
    # returns the real values (so the Key Facts panel + content show originals).
    # The list path stays tokenized.
    if row.pii_protected and row.pii_revealed:
        try:
            import json as _json
            from app import pii_vault
            from app.pii import detokenize
            m = pii_vault.load_mapping(db, row.pk)
            if m:
                if d.get("content"):
                    d["content"] = detokenize(d["content"], m)
                if d.get("extractedFields"):
                    d["extractedFields"] = _json.loads(detokenize(_json.dumps(d["extractedFields"], ensure_ascii=False), m))
        except Exception:  # noqa: BLE001 — never break the read on detok
            pass
    return d


def get_row(db: Session, id_external: str) -> Document | None:
    """Internal — returns the ORM row for routes that need columns the public
    DTO doesn't expose (e.g. s3_key for streaming). Respects vendor scope."""
    tid = get_current_tenant()
    return db.scalar(
        select(Document).where(
            Document.tenant_id == tid,
            Document.id_external == id_external,
            _vendor_clause(),
            _owner_clause(),
        )
    )


def list_for_backup(db: Session) -> list[Document]:
    """M46 · owner-scoped: EVERY doc that still holds a server blob — the
    'free server space' set. Direct uploads (source NULL/upload) get pushed to
    Drive first; docs already in Drive (source='drive', re-pullable) just get
    their server copy purged. The service decides which per doc."""
    tid = get_current_tenant()
    return list(db.scalars(
        select(Document).where(
            Document.tenant_id == tid,
            Document.s3_key.isnot(None),
            _vendor_clause(),
            _owner_clause(),
        ).order_by(Document.pk)
    ).all())


def list_unclassified(db: Session) -> list[Document]:
    """M46 · owner-scoped: docs the closed-enum classifier left weakly typed
    ('other'/empty/null) — the set the type reconciler can improve."""
    from sqlalchemy import or_
    tid = get_current_tenant()
    return list(db.scalars(
        select(Document).where(
            Document.tenant_id == tid,
            or_(Document.doc_type.is_(None),
                Document.doc_type.in_(["", "other", "unknown", "document"])),
            _vendor_clause(),
            _owner_clause(),
        ).order_by(Document.pk)
    ).all())


def count_for_owner(db: Session) -> int:
    """M47 · owner-scoped count of the caller's documents — drives the Drive
    restore prompt (0 ⇒ fresh/wiped account, offer to restore the snapshot)."""
    from sqlalchemy import func
    tid = get_current_tenant()
    return int(db.scalar(
        select(func.count()).select_from(Document).where(
            Document.tenant_id == tid, _vendor_clause(), _owner_clause())) or 0)


def get_row_by_pk(db: Session, pk: int, *, tenant_id: str | None = None) -> Document | None:
    """Tenant-scoped lookup by primary key. Use this instead of
    `db.get(Document, pk)` everywhere — the bare `db.get()` skips the
    tenant filter so a forged or stale pk from another tenant would
    silently leak. Pass `tenant_id` when the caller is outside a
    request context (e.g. an Arq worker job that has its own tenant
    arg); falls back to `get_current_tenant()` otherwise.
    Vendor scope intentionally NOT applied here — agents pulling a
    document for matching need cross-vendor visibility within the
    tenant.
    """
    tid = tenant_id if tenant_id is not None else get_current_tenant()
    # M46 · owner scope is a no-op outside a documents request (worker jobs
    # pass tenant_id and have no user context → owner_user_pk is None). In a
    # documents request it stops one user pulling another's doc by pk.
    return db.scalar(
        select(Document).where(
            Document.tenant_id == tid, Document.pk == pk, _owner_clause()
        )
    )


def chunks_for_doc(
    db: Session,
    document_pk: int,
    *,
    tenant_id: str | None = None,
    limit: int | None = None,
) -> list[DocumentChunk]:
    """Tenant-scoped list of chunks for a document, ordered by chunk_index.
    Replaces ad-hoc `select(DocumentChunk).where(document_pk==...)` in
    agents — those skip the tenant filter and would return chunks for a
    misaddressed cross-tenant document_pk."""
    tid = tenant_id if tenant_id is not None else get_current_tenant()
    stmt = (
        select(DocumentChunk)
        .where(DocumentChunk.tenant_id == tid, DocumentChunk.document_pk == document_pk)
        .order_by(DocumentChunk.chunk_index)
    )
    if limit is not None:
        stmt = stmt.limit(limit)
    return list(db.scalars(stmt).all())


def source_unchanged(db: Session, source_ref: str, modified: str | None) -> bool:
    """True when a doc from this connector source (e.g. a Drive file id) already
    exists with the SAME modifiedTime → the autosync can skip the (expensive)
    re-download. False when modified is unknown (force a fetch) or no match.
    Owner+tenant scoped; includes archived so a deleted-but-unchanged file isn't
    re-pulled every cycle."""
    if not source_ref or not modified:
        return False
    tid = get_current_tenant()
    return db.scalar(
        select(Document.pk).where(
            Document.tenant_id == tid,
            Document.source_ref == source_ref,
            Document.modified == modified,
            _vendor_clause(),
            _owner_clause(),
        ).limit(1)
    ) is not None


def get_by_sha256(db: Session, sha256: str) -> Document | None:
    tid = get_current_tenant()
    # M46 · dedup is per-user in the documents product — two users uploading
    # the same file each get their own copy (different workspaces).
    return db.scalar(
        select(Document).where(
            Document.tenant_id == tid,
            Document.sha256 == sha256,
            _vendor_clause(),
            _owner_clause(),
        )
    )


def create_upload(
    db: Session,
    *,
    id_external: str,
    name: str,
    path: str,
    size: str,
    pages: int,
    mime_type: str,
    sha256: str,
    s3_key: str,
    uploaded_by: str,
    source: str | None = None,
    source_ref: str | None = None,
    retain_original: bool = True,
    modified: str = "just now",
) -> Document:
    tid = get_current_tenant()
    # M17 phase 3 · vendor uploads auto-tag with the uploader's vendor_pk
    # so subsequent reads filter correctly. Admin/reviewer uploads have no
    # vendor_pk attached (they're cross-vendor by default).
    vpk = get_current_vendor_pk()
    # M46 · documents product · stamp the uploading user's pk so the doc lands
    # in their private workspace. None in the auditing product (no per-user scope).
    owner_uid = get_current_owner_user_pk()
    row = Document(
        tenant_id=tid,
        id_external=id_external,
        name=name, path=path, size=size,
        modified=modified,  # display; for connector docs = the source modifiedTime
                            # so autosync can skip unchanged files (see source_unchanged).
        pages=pages, current_page=1,
        type="Uploaded",
        content="pdf",
        s3_key=s3_key, mime_type=mime_type, sha256=sha256, uploaded_by=uploaded_by,
        vendor_pk=vpk,
        owner_user_id=owner_uid,
        source=source,
        source_ref=source_ref,
        retain_original=retain_original,
    )
    db.add(row)
    db.flush()
    return row


def referenced_by_closed_audit(db: Session, id_external: str) -> list[str]:
    """Return the list of CLOSED audit_run id_externals whose requirements
    reference this document. Empty list → safe to hard-delete.

    The audit-history snapshots are aggregate counts, not per-doc, so the
    history itself doesn't reference docs directly. But the underlying
    audit_run rows (closed_at IS NOT NULL) still have audit_run_requirements
    pointing at requirements whose doc_id_external points at this doc.
    Deleting the doc would break the next-cycle clone (which inherits
    doc_id_external from the closed cycle) and break "open in compare"
    on history rows. So we refuse hard-delete in that case and tell the
    caller to archive instead.
    """
    # M49 · the Documents product never creates audit_runs, so this 3-table audit
    # join always returns []. Short-circuit it off the hot hard-delete path.
    from app.config import get_settings as _gs
    if _gs().product == "documents":
        return []
    tid = get_current_tenant()
    closed_audit_ids = db.scalars(
        select(AuditRun.id_external)
        .distinct()
        .join(AuditRunRequirement, AuditRunRequirement.audit_run_pk == AuditRun.pk)
        .join(Requirement,
              (Requirement.pk == AuditRunRequirement.requirement_pk)
              & (Requirement.tenant_id == tid))
        .where(
            AuditRun.tenant_id == tid,
            AuditRun.closed_at.is_not(None),
            (Requirement.doc_id_external == id_external)
            | (Requirement.prior_doc_id_external == id_external),
        )
        .order_by(AuditRun.id_external)
    ).all()
    return list(closed_audit_ids)


def delete_row(db: Session, id_external: str) -> Document | None:
    """Hard-delete a document + cascade-clean every text-FK reference.

    Postgres FK CASCADE handles document_chunks, entities, entity_relations,
    kyc_records, graph_runs, field_edits, document_reviews. But the
    references in `requirements.doc_id_external`, `highlights.doc_id_external`,
    `chat_messages.doc_id_external`, and `diffs.{current,prior}_doc_id_external`
    are TEXT columns (not FKs) and would otherwise dangle. We clean them
    in the same transaction.

    The policy check (referenced_by_closed_audit) lives in the router —
    by the time we get here the caller has decided hard-delete is OK.
    """
    row = get_row(db, id_external)
    if row is None:
        return None
    tid = get_current_tenant()

    # NULL out requirement links — the requirement row keeps its history
    # but loses the dangling doc pointer. UI will then show "no evidence".
    db.execute(
        update(Requirement)
        .where(Requirement.tenant_id == tid, Requirement.doc_id_external == id_external)
        .values(doc_id_external=None, status="todo", confidence=None)
    )
    db.execute(
        update(Requirement)
        .where(Requirement.tenant_id == tid, Requirement.prior_doc_id_external == id_external)
        .values(prior_doc_id_external=None)
    )

    # Drop dangling citation overlays + chat references + diffs.
    db.execute(
        delete(Highlight)
        .where(Highlight.tenant_id == tid, Highlight.doc_id_external == id_external)
    )
    db.execute(
        delete(ChatMessage)
        .where(ChatMessage.tenant_id == tid, ChatMessage.doc_id_external == id_external)
    )
    db.execute(
        delete(Diff)
        .where(
            Diff.tenant_id == tid,
            (Diff.current_doc_id_external == id_external)
            | (Diff.prior_doc_id_external == id_external),
        )
    )

    # M44.P10 · reflexion_pairs link by the doc_id_external TEXT column (no FK),
    # so they'd dangle like the rows above. Delete the ones still bound to this
    # doc. Pairs promoted to tenant-wide knowledge in Phase 1 already had
    # doc_id_external set to NULL, so this purges ONLY the un-promoted
    # doc-specific pairs — exactly the design's Phase-2 split. (Also fixes a
    # latent orphan-row leak on the flag-off path.)
    db.execute(
        delete(ReflexionPair)
        .where(ReflexionPair.tenant_id == tid, ReflexionPair.doc_id_external == id_external)
    )

    db.delete(row)
    db.flush()
    return row


def archive_row(db: Session, id_external: str, *, by_email: str) -> Document | None:
    """Soft-archive · keeps every row + S3 object, just hides from default
    list. Idempotent. Use this when the doc is referenced by a closed
    audit and hard-delete would break history / next-cycle."""
    row = get_row(db, id_external)
    if row is None:
        return None
    if not row.is_archived:
        row.is_archived = True
        row.archived_at = datetime.now(timezone.utc)
        row.archived_by = by_email
        db.flush()
    return row


def unarchive_row(db: Session, id_external: str) -> Document | None:
    row = get_row(db, id_external)
    if row is None:
        return None
    if row.is_archived:
        row.is_archived = False
        row.archived_at = None
        row.archived_by = None
        db.flush()
    return row
