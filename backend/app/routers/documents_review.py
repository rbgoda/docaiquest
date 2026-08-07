from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_current_tenant, get_session
from app.models.documents import Document
from app.repositories import documents as repo
from app.security import CurrentUser, require_role

log = logging.getLogger(__name__)

router = APIRouter()


# ═══════════════════════════════════════════════════════════════════════════
# Helper — snapshots review decision context
# ═══════════════════════════════════════════════════════════════════════════

def _build_review_metadata(db: Session, doc) -> dict:
    """Snapshot the auto-approve decision context at the moment a reviewer
    flips a doc's status. Stored in document_reviews.metadata_json so the
    learning loop can compute calibration over time.

    Captures: extraction confidence, the threshold THAT WAS ACTIVE when the
    decision was made, whether the doc was above/below it, the list of
    review reasons, and how many HITL field edits had been made by the
    time the reviewer signed off."""
    from sqlalchemy import func as _f, select as _s
    from app.document_review import (
        get_document_threshold, get_duplicate_doc_ids, review_reasons,
    )
    from app.orm import FieldEdit

    threshold = get_document_threshold(db)
    dup_ids = get_duplicate_doc_ids(db)
    # M47 · Strip text_layer from API response — only needed for /locate endpoint
    _ef = doc.extracted_fields
    if _ef and "text_layer" in _ef:
        _ef = {k: v for k, v in _ef.items() if k != "text_layer"}
    doc_view = {
        "id": doc.id_external,
        "docType": doc.doc_type,
        "docTypeConfidence": doc.doc_type_confidence,
        "extractedFields": _ef,
        "ingestionStatus": doc.ingestion_status,
    }
    reasons = review_reasons(doc_view, threshold=threshold, duplicate_doc_ids=dup_ids)
    conf = ((doc.extracted_fields or {}).get("confidence"))
    tid = get_current_tenant()
    edit_count = db.scalar(
        _s(_f.count()).select_from(FieldEdit).where(
            FieldEdit.tenant_id == tid, FieldEdit.document_pk == doc.pk,
        )
    ) or 0
    return {
        "extraction_confidence": conf,
        "threshold_at_time": threshold,
        "was_above_threshold": (conf is not None and threshold is not None and conf >= threshold),
        "reasons_at_review": reasons,
        "hitl_edit_count": edit_count,
    }


# ═══════════════════════════════════════════════════════════════════════════
# Quality + locate endpoints
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/{doc_id}/review")
def document_review(doc_id: str, db: Session = Depends(get_session)) -> dict:
    """M47 · HITL review data for ExtractionReview component. Returns quality
    scores + anomalies in the format the frontend expects."""
    from app.agents.quality_detector import QualityDetector
    import asyncio
    row = repo.get_row(db, doc_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Document {doc_id} not found")
    ef = row.extracted_fields or {}
    detector = QualityDetector()
    # extraction_results not available separately — fields already contain bbox info
    return asyncio.run(detector.detect_quality(ef, {}))


@router.get("/{doc_id}/locate")
def document_locate(
    doc_id: str, page: int, x: float, y: float,
    db: Session = Depends(get_session),
) -> dict:
    """M47 · Reverse bbox lookup — given a page + coordinate, return what's there.
    Searches field_bboxes, chunk bboxes, and text blocks. Used by PDF click handler."""
    from sqlalchemy import select
    from app.orm import DocumentChunk

    row = repo.get_row(db, doc_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Document {doc_id} not found")

    results: list[dict] = []

    # 1. Search extracted field bboxes
    ef = row.extracted_fields or {}
    field_bboxes = ef.get("field_bboxes", {})
    for fname, bb in field_bboxes.items():
        if not isinstance(bb, dict):
            continue
        if (bb.get("page") == page
                and bb.get("x0", 0) <= x <= bb.get("x1", 0)
                and bb.get("y0", 0) <= y <= bb.get("y1", 0)):
            results.append({
                "type": "field",
                "name": fname,
                "value": str(ef.get("fields", {}).get(fname, ""))[:200],
                "bbox": {k: bb[k] for k in ("x0","y0","x1","y1","page","page_w","page_h") if k in bb},
                "chunk_pk": bb.get("chunk_pk"),
            })

    # 2. Search chunk bboxes
    chunks = db.execute(
        select(DocumentChunk).where(
            DocumentChunk.document_pk == row.pk,
            DocumentChunk.page == page,
            DocumentChunk.bbox.isnot(None),
        )
    ).scalars().all()
    for c in chunks:
        bb = c.bbox or {}
        if (bb.get("x0", 0) <= x <= bb.get("x1", 0)
                and bb.get("y0", 0) <= y <= bb.get("y1", 0)):
            results.append({
                "type": "chunk",
                "chunk_pk": c.pk,
                "text": (c.text or "")[:200],
                "bbox": bb,
                "kind": c.kind,
            })

    # 3. Search text blocks (word-level from ingestion)
    text_layer = ef.get("text_layer") or []
    for block in text_layer:
        if block.get("page") != page:
            continue
        if (block.get("x0", 0) <= x <= block.get("x1", 0)
                and block.get("y0", 0) <= y <= block.get("y1", 0)):
            results.append({
                "type": block.get("kind", "word"),
                "text": block.get("text", "")[:200],
                "bbox": {k: block[k] for k in ("x0","y0","x1","y1","page","page_w","page_h") if k in block},
            })

    return {"page": page, "x": x, "y": y, "hits": results}


# ═══════════════════════════════════════════════════════════════════════════
# Sign-off endpoints
# ═══════════════════════════════════════════════════════════════════════════

class ReviewPayload(BaseModel):
    """Per-doc sign-off action. status in {'reviewed', 'exception', 'pending'}."""
    status: str
    reason: str | None = None


class BulkReviewPayload(BaseModel):
    doc_ids: list[str]
    status: str
    reason: str | None = None


@router.post("/{doc_id}/review", response_model=Document)
def review_document(
    doc_id: str,
    payload: ReviewPayload,
    background: BackgroundTasks,
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_role("admin", "reviewer")),
) -> dict:
    """Reviewer sign-off on a single document. Flips review_status and
    writes a document_reviews audit row capturing the prior → new
    transition + reason. Accepted statuses: pending / reviewed / exception."""
    from datetime import datetime, timezone
    from app.orm import DocumentReview

    valid = {"pending", "reviewed", "exception"}
    if payload.status not in valid:
        raise HTTPException(status_code=400, detail=f"status must be one of {valid}")

    doc = repo.get_row(db, doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"Document {doc_id} not found")
    if payload.status == "exception" and not (payload.reason or "").strip():
        raise HTTPException(status_code=400, detail="reason required when marking exception")

    prior = doc.review_status
    # Snapshot the decision context for the learning loop. Captured BEFORE
    # mutating doc.review_status so we record "what the reviewer was
    # looking at when they decided". Excludes the auto-approve path (which
    # never reaches this endpoint).
    meta = _build_review_metadata(db, doc)
    doc.review_status = payload.status
    doc.review_note = (payload.reason or "").strip() or None
    doc.reviewed_by = user.email
    doc.reviewed_at = datetime.now(timezone.utc)
    db.add(DocumentReview(
        tenant_id=user.org_id,
        document_pk=doc.pk,
        prior_status=prior,
        new_status=payload.status,
        reviewed_by=user.email,
        reason=doc.review_note,
        metadata_json=meta,
    ))
    db.commit()

    return repo.get(db, doc_id)


@router.post("/review-bulk")
def review_bulk(
    payload: BulkReviewPayload,
    background: BackgroundTasks,
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_role("admin", "reviewer")),
) -> dict:
    """Bulk sign-off on multiple documents at once. Same per-doc semantics
    as POST /{doc_id}/review — each doc gets its own audit-trail row."""
    if get_settings().product == "documents":  # M47 · bulk is a Pro feature
        from app.documents_scope import get_current_owner_user_pk
        from app.services import subscriptions as subs
        subs.enforce_feature(db, owner_user_id=get_current_owner_user_pk(), feature="bulk")
    from datetime import datetime, timezone
    from app.orm import DocumentReview

    valid = {"pending", "reviewed", "exception"}
    if payload.status not in valid:
        raise HTTPException(status_code=400, detail=f"status must be one of {valid}")
    if payload.status == "exception" and not (payload.reason or "").strip():
        raise HTTPException(status_code=400, detail="reason required when marking exception")

    now = datetime.now(timezone.utc)
    note = (payload.reason or "").strip() or None
    updated = 0
    for doc_id in payload.doc_ids:
        doc = repo.get_row(db, doc_id)
        if doc is None:
            continue
        prior = doc.review_status
        meta = _build_review_metadata(db, doc)
        doc.review_status = payload.status
        doc.review_note = note
        doc.reviewed_by = user.email
        doc.reviewed_at = now
        db.add(DocumentReview(
            tenant_id=user.org_id,
            document_pk=doc.pk,
            prior_status=prior,
            new_status=payload.status,
            reviewed_by=user.email,
            reason=note,
            metadata_json=meta,
        ))
        updated += 1
    db.commit()
    return {"updated": updated}
