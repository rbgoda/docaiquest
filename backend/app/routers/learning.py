"""Learning-loop endpoints.

All admin-only — the dashboard surfaces tenant-wide AI performance against
reviewer verdicts and lets the admin nudge the auto-approve threshold.
Nothing here mutates per-audit data; all writes go to `routing_config`.

Routes:
  GET  /api/learning/summary               · overall KPIs
  GET  /api/learning/calibration           · decile bins + observed approval rate
  GET  /api/learning/per-framework         · accuracy split by framework prefix
  GET  /api/learning/disagreements         · row-level AI vs reviewer mismatches
  GET  /api/learning/threshold-suggestion  · F1-optimal auto-approve threshold
  POST /api/learning/apply-threshold       · admin one-click apply suggested value
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app import learning
from app.db import get_session
from app.repositories import routing_configs as rc_repo
from app.security import CurrentUser, require_role

router = APIRouter()


_DEFAULT_AUTO_APPROVE = 0.85  # matches matcher.py fallback when config absent


def _current_auto_approve(db: Session) -> float:
    cfg = rc_repo.get(db) or {}
    try:
        return float(cfg.get("thresholds", {}).get("autoApprove", _DEFAULT_AUTO_APPROVE))
    except (TypeError, ValueError):
        return _DEFAULT_AUTO_APPROVE


@router.get("/summary")
def get_summary(
    db: Session = Depends(get_session),
    _user: CurrentUser = Depends(require_role("admin")),
) -> dict:
    return learning.summary(db)


@router.get("/calibration")
def get_calibration(
    db: Session = Depends(get_session),
    _user: CurrentUser = Depends(require_role("admin")),
) -> list[dict]:
    return learning.calibration(db)


@router.get("/per-framework")
def get_per_framework(
    db: Session = Depends(get_session),
    _user: CurrentUser = Depends(require_role("admin")),
) -> list[dict]:
    return learning.per_framework(db)


@router.get("/disagreements")
def get_disagreements(
    limit: int = 25,
    db: Session = Depends(get_session),
    _user: CurrentUser = Depends(require_role("admin")),
) -> list[dict]:
    return learning.disagreements(db, limit=max(1, min(100, limit)))


@router.get("/threshold-suggestion")
def get_threshold_suggestion(
    db: Session = Depends(get_session),
    _user: CurrentUser = Depends(require_role("admin")),
) -> dict:
    return learning.threshold_suggestion(db, current_threshold=_current_auto_approve(db))


class ApplyThresholdPayload(BaseModel):
    # Bounded · the matcher promotes below ~0.4 are essentially noise, and
    # > 0.99 makes auto-attach effectively impossible (matcher rarely scores
    # that high). The UI's slider matches.
    threshold: float = Field(ge=0.50, le=0.99)


@router.post("/apply-threshold")
def apply_threshold(
    payload: ApplyThresholdPayload,
    db: Session = Depends(get_session),
    _user: CurrentUser = Depends(require_role("admin")),
) -> dict:
    """Write the new auto-approve threshold into routing_config. Preserves
    every other key so we don't accidentally clobber tier configs or rules
    the admin set elsewhere. Returns the merged dict."""
    cfg = rc_repo.get(db) or {}
    thresholds = dict(cfg.get("thresholds") or {})
    thresholds["autoApprove"] = round(payload.threshold, 2)
    cfg["thresholds"] = thresholds
    return rc_repo.upsert(db, cfg)


# ── M28 · Document auto-approve threshold (separate signal from the
# requirement-matching autoApprove above). The feedback loop reads from
# document_reviews.metadata_json snapshots captured at every reviewer flip.


@router.get("/document-threshold-suggestion")
def get_document_threshold_suggestion(
    db: Session = Depends(get_session),
    _user: CurrentUser = Depends(require_role("admin")),
) -> dict:
    """Suggest a documentAutoApprove threshold based on captured human
    decisions. Looks at every non-`ai-auto` review row that carries a
    confidence snapshot, then computes:

      - sample_size       · how many decisions are in scope
      - false_positives   · auto-approve would have been wrong (above-threshold
                            doc that the human marked exception OR edited fields)
      - false_negatives   · auto-approve was too conservative (below-threshold
                            doc the human reviewed clean with zero edits)
      - safe_threshold    · the lowest confidence where ALL human-reviewed
                            samples above it were clean / no-edits. Empty
                            response when there aren't enough samples (<10).
    """
    from sqlalchemy import select
    from app.db import get_current_tenant
    from app.orm import DocumentReview

    tid = get_current_tenant()
    rows = db.execute(
        select(DocumentReview.metadata_json, DocumentReview.new_status,
               DocumentReview.reviewed_by)
        .where(
            DocumentReview.tenant_id == tid,
            DocumentReview.reviewed_by != "ai-auto",
            DocumentReview.metadata_json.is_not(None),
        )
    ).all()
    samples: list[dict] = []
    for meta, status, _by in rows:
        if not meta or meta.get("extraction_confidence") is None:
            continue
        samples.append({
            "confidence": float(meta["extraction_confidence"]),
            "clean": status == "reviewed" and (meta.get("hitl_edit_count") or 0) == 0,
            "exception": status == "exception",
            "edited": (meta.get("hitl_edit_count") or 0) > 0,
            "was_above": bool(meta.get("was_above_threshold")),
        })
    current = _current_document_threshold(db)
    if len(samples) < 10:
        return {
            "current_threshold": current,
            "sample_size": len(samples),
            "suggestion": None,
            "message": "Not enough reviewer decisions yet to suggest a threshold (need ≥10).",
        }
    # Walk candidate thresholds 0.80..0.99 in 0.01 steps. For each, count
    # how many samples above would have been auto-approved correctly (clean)
    # vs incorrectly (exception/edited).
    best = None
    for i in range(80, 100):
        t = i / 100.0
        above = [s for s in samples if s["confidence"] >= t]
        if not above:
            continue
        false_pos = sum(1 for s in above if s["exception"] or s["edited"])
        true_pos = sum(1 for s in above if s["clean"])
        precision = true_pos / max(1, true_pos + false_pos)
        # We want high precision (don't auto-approve wrong docs) and
        # reasonable recall (don't make humans do everything).
        if precision >= 0.95 and (best is None or t < best["threshold"]):
            best = {
                "threshold": round(t, 2),
                "precision": round(precision, 3),
                "would_auto_approve": true_pos,
                "would_block": len(samples) - len(above),
            }
    return {
        "current_threshold": current,
        "sample_size": len(samples),
        "false_positives_now": sum(1 for s in samples if s["was_above"] and (s["exception"] or s["edited"])),
        "false_negatives_now": sum(1 for s in samples if not s["was_above"] and s["clean"]),
        "suggestion": best,
        "message": (
            "Try lowering to capture clean reviews you missed."
            if best and current and best["threshold"] < current
            else "Try raising — auto-approve let through docs the reviewer corrected."
            if best and current and best["threshold"] > current
            else "Current threshold looks calibrated."
        ),
    }


def _current_document_threshold(db: Session) -> float | None:
    cfg = rc_repo.get(db) or {}
    val = (cfg.get("thresholds") or {}).get("documentAutoApprove")
    try:
        return float(val) if val is not None else None
    except (ValueError, TypeError):
        return None


class ApplyDocumentThresholdPayload(BaseModel):
    threshold: float | None = Field(default=None)


@router.post("/document-apply-threshold")
def apply_document_threshold(
    payload: ApplyDocumentThresholdPayload,
    db: Session = Depends(get_session),
    _user: CurrentUser = Depends(require_role("admin")),
) -> dict:
    """Set (or clear) the documentAutoApprove threshold. None = feature off.
    Otherwise must be in [0.80, 0.99]."""
    if payload.threshold is not None:
        if not (0.80 <= payload.threshold <= 0.99):
            from fastapi import HTTPException
            raise HTTPException(status_code=400,
                                detail="threshold must be between 0.80 and 0.99 (or null to disable)")
    cfg = rc_repo.get(db) or {}
    thresholds = dict(cfg.get("thresholds") or {})
    thresholds["documentAutoApprove"] = (
        round(payload.threshold, 2) if payload.threshold is not None else None
    )
    cfg["thresholds"] = thresholds
    return rc_repo.upsert(db, cfg)
