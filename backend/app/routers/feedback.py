"""App-level product feedback — the 'Send feedback' screen.

POST /api/feedback        — submit product feedback (rating + category + comments
                            + suggestion). GET /api/feedback/mine — the user's own
submissions. Reviewed + resolved in the superadmin console (routers/superadmin.py).
Documents-product only, tenant + owner scoped. Mirrors the XpenseAIQ app-level
feedback model (distinct from chat-answer ratings in documents_feedback.py).
"""
from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_current_tenant, get_session
from app.documents_scope import get_current_owner_user_pk
from app.orm import ProductFeedback
from app.security import CurrentUser, get_current_user

router = APIRouter()

_CATEGORIES = {"general", "bug", "idea", "suggestion", "praise", "other"}


def _guard() -> None:
    if get_settings().product != "documents":
        raise HTTPException(status_code=404, detail="Not available")


class FeedbackIn(BaseModel):
    rating: int | None = None              # 1-5 stars (optional)
    category: str = "general"              # general|bug|suggestion|praise|other
    comments: str | None = None
    suggestion: str | None = None
    page: str | None = None                # the screen the user was on
    appVersion: str | None = None
    deviceInfo: str | None = None
    screenshots: list[str] | None = None   # up to 3 client-compressed JPEG data URLs


def _to_dict(f: ProductFeedback) -> dict:
    return {
        "id": f.pk, "rating": f.rating, "category": f.category,
        "comments": f.comments, "suggestion": f.suggestion, "page": f.page,
        "status": f.status, "hasIssues": f.has_issues, "resolution": f.resolution,
        "createdAt": f.created_at.isoformat() if f.created_at else None,
    }


def _next_feedback_ref(db: Session, tenant_id: str) -> str:
    """Versioned feedback id "1.1.<patch>.<seq>". patch = #resolved-or-verified at creation, so
    the main version 1.1.<patch> grows as feedback is resolved; seq counts within that patch."""
    from sqlalchemy import func
    patch = db.scalar(select(func.count()).select_from(ProductFeedback).where(
        ProductFeedback.tenant_id == tenant_id,
        ProductFeedback.status.in_(["resolved", "verified"]))) or 0
    seq = (db.scalar(select(func.count()).select_from(ProductFeedback).where(
        ProductFeedback.tenant_id == tenant_id,
        ProductFeedback.ref.like(f"1.1.{patch}.%"))) or 0) + 1
    return f"1.1.{patch}.{seq}"


@router.post("/feedback")
def submit(payload: FeedbackIn, background_tasks: BackgroundTasks,
           db: Session = Depends(get_session),
           user: CurrentUser = Depends(get_current_user)) -> dict:
    _guard()
    cat = (payload.category or "general").lower()
    if cat not in _CATEGORIES:
        cat = "other"
    if payload.rating is not None and not (1 <= int(payload.rating) <= 5):
        raise HTTPException(status_code=400, detail="rating must be between 1 and 5")
    comments = (payload.comments or "").strip()
    suggestion = (payload.suggestion or "").strip()
    if not (payload.rating or comments or suggestion):
        raise HTTPException(status_code=400, detail="Provide a rating, a comment, or a suggestion")
    # Screenshots: max 3, KEEP as many as fit under ~8MB total (previously dropped ALL when
    # over the cap, so a single hi-res shot vanished silently). Keep what fits, in order.
    _shots_in = [s for s in (payload.screenshots or []) if isinstance(s, str)][:3]
    shots, _tot = [], 0
    for s in _shots_in:
        if _tot + len(s) > 8 * 1024 * 1024:
            break
        shots.append(s)
        _tot += len(s)
    _tid = get_current_tenant()
    row = ProductFeedback(
        tenant_id=_tid,
        ref=_next_feedback_ref(db, _tid),
        owner_user_id=get_current_owner_user_pk(),
        email=getattr(user, "email", None),
        rating=int(payload.rating) if payload.rating else None,
        category=cat,
        comments=comments[:4000] or None,
        suggestion=suggestion[:4000] or None,
        page=(payload.page or "")[:64] or None,
        app_version=(payload.appVersion or "")[:32] or None,
        device_info=(payload.deviceInfo or "")[:255] or None,
        screenshots=shots or None,
        has_issues=(cat == "bug"),
        status="new",
    )
    db.add(row)
    db.commit()
    # Auto-triage off the request path (LLM draft + status→in_progress); best-effort.
    from app.services import feedback_triage
    background_tasks.add_task(feedback_triage.triage_feedback, row.pk, get_current_tenant())
    return {"ok": True, "id": row.pk}


@router.get("/feedback/mine")
def my_feedback(db: Session = Depends(get_session),
                user: CurrentUser = Depends(get_current_user)) -> dict:
    _guard()
    rows = db.scalars(
        select(ProductFeedback)
        .where(ProductFeedback.tenant_id == get_current_tenant(),
               ProductFeedback.owner_user_id == get_current_owner_user_pk())
        .order_by(desc(ProductFeedback.pk)).limit(50)
    ).all()
    return {"feedback": [_to_dict(r) for r in rows]}
