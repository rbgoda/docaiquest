from __future__ import annotations

import logging
import secrets

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import storage
from app.config import get_settings
from app.db import get_current_tenant, get_session
from app.models.documents import Document
from app.queue import enqueue_ingest
from app.repositories import documents as repo
from app.security import CurrentUser, require_role
from app.services import documents as docs_service
# Re-exports — kept so any caller still using the underscore-prefixed
# helper names continues to work (TODO #25 conservative extraction).
_link_doc_to_requirement = docs_service.link_doc_to_requirement
_human_size = docs_service.human_size

import logging as _logging  # noqa: E402
log = _logging.getLogger(__name__)

router = APIRouter()


def _count_upload_pages(raw: bytes, mime: str) -> int | None:
    """Best-effort page count from the buffered upload bytes WITHOUT a full parse —
    only needs to be cheap + correct for the abuse case (big PDFs). Returns None for
    types we can't cheaply count (they stay bounded by the document cap)."""
    m = (mime or "").lower()
    try:
        if "pdf" in m:
            import fitz
            with fitz.open(stream=raw, filetype="pdf") as d:
                return int(d.page_count)
        if m.startswith("image/"):
            try:
                import io as _io

                from PIL import Image
                with Image.open(_io.BytesIO(raw)) as im:
                    return int(getattr(im, "n_frames", 1) or 1)
            except Exception:  # noqa: BLE001
                return 1
    except Exception:  # noqa: BLE001 — never block an upload on a counting hiccup
        return None
    return None


@router.post("", response_model=Document, status_code=201)
async def upload_document(
    file: UploadFile = File(...),
    # Optional · when the upload originated from a per-row "Upload" button on
    # a specific requirement, the frontend passes that requirement id here.
    # We pin the document to it BEFORE ingestion — the matcher still runs to
    # populate confidence + reasoning, but the link is fixed by the vendor's
    # explicit intent. Without this, vendors could click Upload on REQ-CC6.2
    # and have their MFA screenshot land on REQ-CC6.7 because the matcher
    # disagreed — a trust-killer for the vendor UX.
    requirement_id: str | None = Form(default=None),
    # When the upload happens inside a vendor's portal page (Documents tab,
    # Expenses tab, vendor-detail upload button), the frontend passes the
    # active vendor's pk so downstream reconciliation / graph queries can
    # scope by vendor. Falls back to NULL — vendor_pk can be backfilled
    # later via the UPDATE pattern in /api/admin/vendors/{id}/claim-docs.
    vendor_pk: int | None = Form(default=None),
    db: Session = Depends(get_session),
    # Vendor role is allowed: vendors upload evidence against requirements
    # they're scoped to. Tenant + vendor scoping enforced at repo layer.
    user: CurrentUser = Depends(require_role("admin", "reviewer", "vendor")),
) -> dict:
    settings = get_settings()

    # M44.P9.12 · per-user upload rate limit · 20 / 5min.
    from app.rate_limit import rate_limit as _rate_limit
    _rate_limit(user.email, action="doc_upload")

    # §compliance · the documents product requires a one-time acknowledgement
    # that uploaded files may contain personal / special-category (health) data,
    # before the FIRST upload. The frontend catches this 403 and shows the
    # acknowledgement, then POSTs /me/consent and retries.
    if settings.product == "documents":
        from app.documents_scope import get_current_owner_user_pk
        from app.services import consent as consent_svc
        from app.services import subscriptions as subs
        uid = get_current_owner_user_pk()
        # M47 · free-plan document cap.
        if uid is not None:
            subs.enforce_upload(db, tenant_id=settings.tenant_id, owner_user_id=uid)
        if uid is not None and not consent_svc.has_current(
                db, tenant_id=settings.tenant_id, user_id=uid,
                kind=consent_svc.KIND_PERSONAL_DATA):
            raise HTTPException(
                status_code=403,
                detail={"code": "consent_required",
                        "message": "Please acknowledge that your documents may contain "
                                   "personal or health data before uploading."},
            )
        # M47 · free-tier only — model-training consent before the first upload.
        # Free uploads may be used to improve our models; paid plans never train on
        # user data, so this is demanded only when the effective plan is 'free'.
        if uid is not None:
            from app.orm import User as _User
            _u = db.get(_User, uid)
            if (_u is not None and subs.effective_plan(_u) == "free"
                    and not consent_svc.has_current(db, tenant_id=settings.tenant_id,
                                                    user_id=uid, kind=consent_svc.KIND_MODEL_TRAINING)):
                raise HTTPException(
                    status_code=403,
                    detail={"code": "training_consent_required",
                            "message": "On the free plan, your uploads may be used to improve our AI "
                                       "models. Please acknowledge to continue — or upgrade for full "
                                       "privacy (paid plans are never used for training)."},
                )

    # M36 · free-tier monthly doc cap. No-op for paid tenants.
    from app.plan_limits import check_can_upload_document, record_document_uploaded
    check_can_upload_document(db)

    # Read + hash in one pass. Enforces the size cap before we hit storage.
    try:
        raw, sha = storage.hash_and_buffer(file.file, settings.max_upload_bytes)
    except ValueError as e:
        raise HTTPException(status_code=413, detail=str(e))

    # Authoritative MIME from magic bytes. A `.pdf` renamed `.exe` (or
    # the reverse) gets refused here regardless of what the browser
    # claimed. CSV is allowed by extension since it has no magic bytes.
    try:
        verified_mime = storage.validate_upload(raw, file.content_type, file.filename)
    except ValueError as e:
        raise HTTPException(status_code=415, detail=str(e))

    # Cheap page count from the buffered bytes (PyMuPDF page_count, <1s).
    # Stored on the Document row so the strategist can use it BEFORE ingestion
    # starts. Previously computed but thrown away (placeholder pages=1).
    _upload_pages = _count_upload_pages(raw, verified_mime) or 1

    # M47 · free-plan per-document PAGE cap — reject a document with more pages than
    # the plan allows BEFORE storing it, so a 100-page PDF can't slip past.
    if settings.product == "documents":
        from app.documents_scope import get_current_owner_user_pk as _guid
        from app.services import subscriptions as _subs
        _uid = _guid()
        if _uid is not None:
            _subs.enforce_pages(db, owner_user_id=_uid, pages=_upload_pages)

    safe_name = storage.sanitize_filename(file.filename)

    # Idempotency: re-uploading the same bytes is a no-op (returns existing
    # row). If the caller supplied a `requirement_id` for the dup upload,
    # honor it by pinning the existing doc — useful when the vendor uploads
    # the same PDF against two different controls (e.g. a master policy
    # that covers REQ-CC6.1 AND REQ-CC6.2).
    existing = repo.get_by_sha256(db, sha)
    if existing is not None:
        if requirement_id:
            _link_doc_to_requirement(db, existing.id_external, requirement_id)
        return repo._to_dict(existing)  # noqa: SLF001

    # Generate a stable S3 key under the tenant prefix so storage is self-describing.
    tenant = get_current_tenant()
    suffix = secrets.token_hex(8)
    s3_key = f"{tenant}/documents/{sha[:2]}/{sha}-{suffix}"

    import io
    storage.put_object(s3_key, io.BytesIO(raw), content_type=verified_mime)

    # External ID — match the seed format `doc-*` so all downstream code paths
    # (highlights, citations, frontend lookups) work uniformly. A random suffix
    # keeps it UNIQUE per upload: dedup (sha) already ran above, so we only
    # reach here for a genuinely new row — but with per-user workspaces two
    # different owners can legitimately hold the same file, and the
    # (tenant_id, id_external) uniqueness is tenant-wide, so a content-only id
    # would collide on the second owner's upload (M46).
    id_external = f"doc-up-{sha[:10]}-{secrets.token_hex(3)}"

    # Pages: counted cheaply at upload (PyMuPDF page_count, <1s). Ingest will
    # set the authoritative count after full parse; this estimate lets the
    # DocumentStrategist route to the right pipeline BEFORE heavy processing.
    try:
        row = repo.create_upload(
            db,
            id_external=id_external,
            name=safe_name,
            path=f"Uploads › {user.email}",
            size=_human_size(len(raw)),
            pages=_upload_pages,  # cheap estimate — worker refines during ingestion
            mime_type=verified_mime,
            sha256=sha,
            s3_key=s3_key,
            uploaded_by=user.email,
        )
    except IntegrityError:
        # Concurrent double-upload of the same file (mig 0080 partial-unique index).
        # The other request won the race — return its row instead of erroring.
        db.rollback()
        existing = repo.get_by_sha256(db, sha)
        if existing is not None:
            if requirement_id:
                _link_doc_to_requirement(db, existing.id_external, requirement_id)
            return repo._to_dict(existing)  # noqa: SLF001
        raise
    # Pin to the originating requirement BEFORE enqueuing the matcher so the
    # job sees the link and skips re-attaching. Idempotent if the link
    # already exists.
    if requirement_id:
        _link_doc_to_requirement(db, row.id_external, requirement_id)
    # Assign vendor_pk so per-vendor reconciliation + graph scoping work
    # without manual backfill. The fact_extractor and graph bootstrap read
    # this field directly off `documents.vendor_pk`.
    if vendor_pk is not None:
        row.vendor_pk = vendor_pk

    # Kick off the ingestion pipeline (worker). The status flips to
    # "processing" → "ready" via /status polling on the frontend side.
    row.ingestion_status = "pending"
    # M36 · count this against the free-tier monthly cap (no-op for paid).
    record_document_uploaded(db)
    db.commit()
    try:
        await enqueue_ingest(row.pk, tenant)
    except Exception as e:  # noqa: BLE001 — Redis hiccup: mark failed now, don't strand as 'pending'
        logging.getLogger("docaiq.routers.documents").warning(
            "enqueue_ingest failed for doc pk=%s: %s — marking failed", row.pk, e)
        row.ingestion_status = "failed"
        row.ingestion_error = "Could not queue the document for processing — please re-upload."
        db.commit()
    return repo._to_dict(row)  # noqa: SLF001




# ---- Paste-a-share-link upload ----------------------------------------------
# Vendors often keep evidence in Google Drive / Dropbox / OneDrive folders.
# Forcing them to download-then-reupload is wasted work. This endpoint
# accepts a public share URL — single PDF, Drive folder, or any zip — and
# runs every PDF it finds through the existing ingestion + matcher pipeline.
#
# Provider-specific logic lives in `app/link_pull.py`; the router just
# loops the result list into the same upload path as direct file uploads.
#
# Limits by design: links must be public ("Anyone with link → Viewer" on
# Drive, equivalent on Dropbox/OneDrive). We are NOT doing OAuth here —
# that's a separate, larger feature (see HANDBOOK Q on connectors).

from pydantic import BaseModel, HttpUrl  # noqa: E402 — local import keeps the top file tidy
from app.link_pull import (  # noqa: E402
    LinkPullError,
    PulledFile,
    classify_link,
    pull_drive_folder,
    pull_single_pdf,
    pull_zip,
)


class DocumentFromLinkPayload(BaseModel):
    url: HttpUrl
    # Optional · pin to a specific requirement just like the per-row Upload
    # button does. Only applied when the pull yields exactly ONE file —
    # for folders / zips, the matcher decides where each doc lands (pinning
    # 50 different PDFs to the same requirement would be wrong).
    requirementId: str | None = None


class LinkPullSummary(BaseModel):
    """Result of a /from-link call. Always returns a list since folder /
    zip pulls produce N docs; single-PDF pulls produce 1. The frontend
    surfaces `created` count, `skipped` (duplicates), and any per-file
    errors so the vendor knows what went through."""
    kind: str  # "single_file" | "drive_folder" | "zip"
    requested_url: str
    created: list[Document]
    skipped: list[str]   # filenames that were duplicates (already in tenant)
    errors: list[str]    # per-file ingest errors


async def _ingest_one(
    db: Session,
    pulled: PulledFile,
    tenant: str,
    uploader_email: str,
    requirement_id: str | None,
) -> tuple[dict | None, str | None]:
    """Sha + dedupe + S3 + DB + enqueue — same path as direct upload. Returns
    `(doc_dict_or_None, error_or_None)`. Caller buckets into created/skipped/errors."""
    import hashlib
    import io as _io
    settings = get_settings()
    if len(pulled.body) > settings.max_upload_bytes:
        return None, f"{pulled.filename}: file is {_human_size(len(pulled.body))}; cap is {_human_size(settings.max_upload_bytes)}"

    sha = hashlib.sha256(pulled.body).hexdigest()
    existing = repo.get_by_sha256(db, sha)
    if existing is not None:
        if requirement_id:
            _link_doc_to_requirement(db, existing.id_external, requirement_id)
            db.commit()
        return repo._to_dict(existing), None  # noqa: SLF001  — caller decides bucket

    suffix = secrets.token_hex(8)
    s3_key = f"{tenant}/documents/{sha[:2]}/{sha}-{suffix}"
    try:
        storage.put_object(s3_key, _io.BytesIO(pulled.body), content_type=pulled.content_type)
    except Exception as e:
        return None, f"{pulled.filename}: storage write failed · {e}"

    # Random suffix → unique per upload across owners (see upload_document).
    id_external = f"doc-up-{sha[:10]}-{secrets.token_hex(3)}"
    try:
        row = repo.create_upload(
            db,
            id_external=id_external,
            name=pulled.filename,
            path=f"Link from {uploader_email}",
            size=_human_size(len(pulled.body)),
            pages=1,
            mime_type=pulled.content_type or "application/pdf",
            sha256=sha,
            s3_key=s3_key,
            uploaded_by=uploader_email,
        )
        if requirement_id:
            _link_doc_to_requirement(db, row.id_external, requirement_id)
        row.ingestion_status = "pending"
        db.commit()
    except IntegrityError:
        # Concurrent pull/upload of the SAME file+owner (partial-unique index, mig 0080).
        # The other request won — return its row instead of 500-ing with a poisoned txn
        # (mirrors the direct-upload path). No re-enqueue; the winner already ingests it.
        db.rollback()
        existing = repo.get_by_sha256(db, sha)
        if existing is not None:
            if requirement_id:
                _link_doc_to_requirement(db, existing.id_external, requirement_id)
            return repo._to_dict(existing), None
        raise
    try:
        await enqueue_ingest(row.pk, tenant)
    except Exception as e:  # noqa: BLE001 — Redis hiccup: mark failed now, don't strand as 'pending'
        logging.getLogger("docaiq.routers.documents").warning(
            "enqueue_ingest failed for doc pk=%s: %s — marking failed", row.pk, e)
        row.ingestion_status = "failed"
        row.ingestion_error = "Could not queue the document for processing — please re-upload."
        db.commit()
    return repo._to_dict(row), None


@router.post("/from-link", response_model=LinkPullSummary, status_code=201)
async def upload_documents_from_link(
    payload: DocumentFromLinkPayload,
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_role("admin", "reviewer", "vendor")),
) -> LinkPullSummary:
    """Single PDF, Drive folder, or zip — auto-detected from the URL.

    Returns a summary listing every doc created, every duplicate skipped,
    and any per-file errors. For folders / zips, the matcher runs per-doc
    after ingestion (existing pipeline); the link-pull endpoint just gets
    the bytes in. The frontend shows progress as docs land.

    `requirementId` is honored ONLY for single-file pulls. Multi-file pulls
    leave the matcher to decide per-file routing — pinning every file in a
    50-PDF folder to the same requirement would be wrong."""
    raw_url = str(payload.url)
    kind = classify_link(raw_url)

    try:
        if kind == "drive_folder":
            pulled = await pull_drive_folder(raw_url)
        elif kind == "zip":
            pulled = await pull_zip(raw_url)
        else:
            pulled = await pull_single_pdf(raw_url)
    except LinkPullError as e:
        raise HTTPException(status_code=e.http_status, detail=str(e))

    pin_req_id = payload.requirementId if len(pulled) == 1 else None

    tenant = get_current_tenant()
    created: list[dict] = []
    skipped: list[str] = []
    errors: list[str] = []

    for f in pulled:
        existing_before = repo.get_by_sha256(db, __import__("hashlib").sha256(f.body).hexdigest())
        result, err = await _ingest_one(db, f, tenant, user.email, pin_req_id)
        if err:
            errors.append(err)
        elif existing_before is not None:
            skipped.append(f.filename)
        else:
            created.append(result)

    return LinkPullSummary(
        kind=kind,
        requested_url=raw_url,
        created=[Document(**c) for c in created],
        skipped=skipped,
        errors=errors,
    )


