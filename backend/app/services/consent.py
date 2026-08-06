"""M46 · §compliance · consent capture helpers.

Two consent kinds:
  · processing    — at signup. Processing + third-party LLM sub-processors.
  · personal_data — one-time, before the first upload. Acknowledges that
                    documents may contain personal / special-category (health) data.

Bump CONSENT_VERSION when the consent text materially changes — users are then
re-prompted (their old-version row no longer satisfies the current requirement).
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.orm import ConsentRecord

CONSENT_VERSION = "2026-06-04"
KIND_PROCESSING = "processing"
KIND_PERSONAL_DATA = "personal_data"
# M47 · free-tier only. Required before a FREE user's first upload: free-plan
# uploads may be used to improve our AI models (schema learning, eval). Paid plans
# never train on user data — so this consent is only demanded when the effective
# plan is 'free'.
KIND_MODEL_TRAINING = "model_training"


def record(db: Session, *, tenant_id: str, user_id: int, kind: str) -> None:
    """Upsert the user's consent for `kind` at the current version."""
    row = db.scalar(select(ConsentRecord).where(
        ConsentRecord.tenant_id == tenant_id,
        ConsentRecord.user_id == user_id,
        ConsentRecord.kind == kind,
    ))
    if row is None:
        db.add(ConsentRecord(tenant_id=tenant_id, user_id=user_id,
                             kind=kind, version=CONSENT_VERSION))
    else:
        row.version = CONSENT_VERSION
    db.flush()


def has_current(db: Session, *, tenant_id: str, user_id: int, kind: str) -> bool:
    """True when the user has accepted `kind` at the CURRENT version."""
    row = db.scalar(select(ConsentRecord).where(
        ConsentRecord.tenant_id == tenant_id,
        ConsentRecord.user_id == user_id,
        ConsentRecord.kind == kind,
    ))
    return bool(row and row.version == CONSENT_VERSION)


def status(db: Session, *, tenant_id: str, user_id: int) -> dict:
    rows = db.scalars(select(ConsentRecord).where(
        ConsentRecord.tenant_id == tenant_id,
        ConsentRecord.user_id == user_id,
    )).all()
    by_kind = {r.kind: r for r in rows}
    return {
        "version": CONSENT_VERSION,
        "processing": bool(by_kind.get(KIND_PROCESSING) and by_kind[KIND_PROCESSING].version == CONSENT_VERSION),
        "personalData": bool(by_kind.get(KIND_PERSONAL_DATA) and by_kind[KIND_PERSONAL_DATA].version == CONSENT_VERSION),
        "modelTraining": bool(by_kind.get(KIND_MODEL_TRAINING) and by_kind[KIND_MODEL_TRAINING].version == CONSENT_VERSION),
    }
