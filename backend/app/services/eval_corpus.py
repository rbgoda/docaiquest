"""Golden eval corpus — capture + export labeled extraction samples.

Captures one snapshot per CONSENTED free-tier document (its extraction: doc_type +
fields + confidence + trust) into `golden_eval_cases`, so we can build/curate a real,
diverse evaluation set and track coverage by doc type. A case flips `verified` when a
human corrects/confirms the fields (ground truth via the field-edit flow).

Consent-gated (KIND_MODEL_TRAINING) — paid documents are NEVER captured. Superadmin-
export only; field values may contain PII, which the free-plan training consent covers.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_current_tenant
from app.orm import GoldenEvalCase, User

log = logging.getLogger("docaiq.eval_corpus")


def is_training_eligible(db: Session, owner_user_id: int | None) -> bool:
    """True only for a consented free-plan owner (effective_plan==free +
    KIND_MODEL_TRAINING). The single privacy gate for corpus capture."""
    if not owner_user_id:
        return False
    try:
        from app.services import consent as _consent
        from app.services import subscriptions as _subs
        u = db.get(User, owner_user_id)
        if u is None or _subs.effective_plan(u) != "free":
            return False
        return _consent.has_current(db, tenant_id=get_current_tenant(),
                                    user_id=owner_user_id, kind=_consent.KIND_MODEL_TRAINING)
    except Exception:  # noqa: BLE001
        return False


def capture_case(db: Session, doc) -> bool:
    """Upsert a golden eval case from a consented free doc's extraction. No-op for
    ineligible docs or docs without an extraction. Best-effort; caller commits."""
    ef = getattr(doc, "extracted_fields", None)
    if not isinstance(ef, dict) or not ef.get("fields"):
        return False
    if not is_training_eligible(db, getattr(doc, "owner_user_id", None)):
        return False
    fld = ef.get("fields") if isinstance(ef.get("fields"), dict) else {}
    trust = ef.get("trust") if isinstance(ef.get("trust"), dict) else {}
    payload = dict(
        doc_id_external=getattr(doc, "id_external", None),
        doc_type=ef.get("doc_type"),
        detected_doc_type=(fld.get("detected_doc_type") if isinstance(fld, dict) else None),
        fields=fld,
        field_confidence=(ef.get("field_confidence") or {}),
        trust_score=(trust.get("score") if isinstance(trust, dict) else None),
    )
    row = db.scalar(select(GoldenEvalCase).where(
        GoldenEvalCase.tenant_id == doc.tenant_id, GoldenEvalCase.document_pk == doc.pk))
    if row is None:
        db.add(GoldenEvalCase(tenant_id=doc.tenant_id, document_pk=doc.pk,
                              source="free_consented", **payload))
    else:
        for k, v in payload.items():
            setattr(row, k, v)
        row.updated_at = datetime.now(timezone.utc)
    db.flush()
    return True


def mark_verified(db: Session, document_pk: int) -> None:
    """A human edited/confirmed this doc's fields → its case is ground-truth verified.
    Best-effort no-op when no case exists (e.g. paid doc / not captured)."""
    row = db.scalar(select(GoldenEvalCase).where(
        GoldenEvalCase.tenant_id == get_current_tenant(),
        GoldenEvalCase.document_pk == document_pk))
    if row is None:
        return
    row.verified = True
    row.edit_count = (row.edit_count or 0) + 1
    row.updated_at = datetime.now(timezone.utc)
    db.flush()


def _to_record(r: GoldenEvalCase) -> dict:
    return {
        "docType": r.doc_type,
        "detectedDocType": r.detected_doc_type,
        "trustScore": r.trust_score,
        "verified": bool(r.verified),
        "editCount": r.edit_count,
        "fields": r.fields or {},
        "fieldConfidence": r.field_confidence or {},
    }


def export_cases(db: Session, tenant_id: str, *, verified_only: bool = False,
                 limit: int = 5000) -> list[dict]:
    """Eval records for the corpus (most-recent first). `verified_only` restricts to
    human-confirmed ground-truth cases — the trustworthy subset for a golden set."""
    stmt = select(GoldenEvalCase).where(GoldenEvalCase.tenant_id == tenant_id)
    if verified_only:
        stmt = stmt.where(GoldenEvalCase.verified.is_(True))
    stmt = stmt.order_by(GoldenEvalCase.updated_at.desc()).limit(limit)
    return [_to_record(r) for r in db.scalars(stmt).all()]


def coverage(db: Session, tenant_id: str) -> dict:
    """Corpus size + verified count + per-doc-type breakdown (the coverage map)."""
    total = int(db.scalar(select(func.count()).select_from(GoldenEvalCase).where(
        GoldenEvalCase.tenant_id == tenant_id)) or 0)
    verified = int(db.scalar(select(func.count()).select_from(GoldenEvalCase).where(
        GoldenEvalCase.tenant_id == tenant_id, GoldenEvalCase.verified.is_(True))) or 0)
    rows = db.execute(
        select(GoldenEvalCase.doc_type, func.count())
        .where(GoldenEvalCase.tenant_id == tenant_id)
        .group_by(GoldenEvalCase.doc_type)).all()
    by_type = {(dt or "unknown"): int(c) for dt, c in rows}
    return {"total": total, "verified": verified, "byDocType": by_type}
