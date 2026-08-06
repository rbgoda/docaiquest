"""M46 · Documents System · chat answer feedback (thumbs + free text).

POST /api/chat-feedback  — record 👍/👎 (+ optional text on 👎). 👎 also demotes
the answer in the reflexion cache so it isn't reused; the text is logged for the
improvement loop. GET /api/chat-feedback — recent feedback (the improvement
queue). Documents-product only, owner-scoped.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_current_tenant, get_session
from app.documents_scope import get_current_owner_user_pk
from app.orm import ChatFeedback, ChatMessage
from app.security import CurrentUser, get_current_user

router = APIRouter()


def _guard() -> None:
    if get_settings().product != "documents":
        raise HTTPException(status_code=404, detail="Not available")


class FeedbackPayload(BaseModel):
    messagePk: int
    direction: str            # "up" | "down"
    feedback: str | None = None
    # M46 · rich feedback box fields (the xpenseaiq-style modal on 👎).
    category: str | None = None      # "wrong" | "incomplete" | "offtopic" | "other"
    suggestion: str | None = None    # "what would the right answer be?"
    rating: int | None = None        # optional 1–5 stars
    screenshots: list[str] | None = None  # up to 3 compressed JPEG data URLs


@router.post("/chat-feedback")
def submit_feedback(payload: FeedbackPayload, db: Session = Depends(get_session),
                    user: CurrentUser = Depends(get_current_user)) -> dict:
    _guard()
    if payload.direction not in ("up", "down"):
        raise HTTPException(status_code=400, detail="direction must be 'up' or 'down'")
    tid = get_current_tenant()
    msg = db.scalar(select(ChatMessage).where(
        ChatMessage.pk == payload.messagePk, ChatMessage.tenant_id == tid))
    if msg is None:
        raise HTTPException(status_code=404, detail="Chat message not found")
    # Owner-scope guard · can't leave feedback on (or copy the answer text of)
    # another user's message by guessing its pk. M46 isolation hardening.
    from app.routers.doc_chat import _assert_message_visible
    _assert_message_visible(db, msg, user)

    cat = (payload.category or "").strip().lower() or None
    if cat is not None and cat not in ("wrong", "incomplete", "offtopic", "other"):
        cat = "other"
    rating = payload.rating
    if rating is not None and not (1 <= int(rating) <= 5):
        rating = None
    # Screenshots · keep at most 3 image data URLs, each ≤ ~1.5MB after the
    # client-side compression, so a stray paste can't bloat the row.
    shots: list[str] | None = None
    if payload.screenshots:
        shots = [s for s in payload.screenshots
                 if isinstance(s, str) and s.startswith("data:image/") and len(s) <= 1_500_000][:3]
        shots = shots or None
    db.add(ChatFeedback(
        tenant_id=tid, owner_user_id=get_current_owner_user_pk(),
        message_pk=payload.messagePk, direction=payload.direction,
        feedback=(payload.feedback or "").strip() or None,
        category=cat,
        suggestion=(payload.suggestion or "").strip() or None,
        rating=int(rating) if rating is not None else None,
        screenshots=shots,
        answer_excerpt=(msg.text or "")[:1000], doc_id=msg.doc_id_external,
    ))
    # Immediate learning · demote/promote in the reflexion cache (per-doc msgs
    # that have a reflexion row; no-op for workspace messages).
    try:
        from app.routers.doc_chat import _vote_on_chat_message
        _vote_on_chat_message(db, payload.messagePk, user, +1 if payload.direction == "up" else -1)
    except Exception:  # noqa: BLE001
        pass
    # Chat-faithfulness corpus · a 👍/👎 is a human label → attach it to the case
    # (no-op when there's no case, e.g. a paid chat). Best-effort.
    try:
        from app.services import faithfulness_corpus
        faithfulness_corpus.attach_label(db, payload.messagePk, direction=payload.direction,
                                         category=cat, suggestion=payload.suggestion, rating=rating)
    except Exception:  # noqa: BLE001
        pass
    db.commit()
    return {"recorded": True}


@router.get("/chat-feedback")
def list_feedback(limit: int = 50, db: Session = Depends(get_session),
                  user: CurrentUser = Depends(get_current_user)) -> dict:
    """The improvement queue — recent feedback, newest first (owner-scoped)."""
    _guard()
    tid = get_current_tenant()
    uid = get_current_owner_user_pk()
    base = [ChatFeedback.tenant_id == tid]
    if uid is not None:
        base.append(ChatFeedback.owner_user_id == uid)
    rows = db.scalars(
        select(ChatFeedback).where(*base).order_by(desc(ChatFeedback.created_at)).limit(min(limit, 200))
    ).all()
    down = db.scalar(select(func.count()).select_from(ChatFeedback)
                     .where(*base, ChatFeedback.direction == "down")) or 0
    up = db.scalar(select(func.count()).select_from(ChatFeedback)
                   .where(*base, ChatFeedback.direction == "up")) or 0
    return {
        "up": int(up), "down": int(down),
        "feedback": [{
            "id": r.pk, "direction": r.direction, "feedback": r.feedback,
            "category": r.category, "suggestion": r.suggestion, "rating": r.rating,
            "screenshotCount": len(r.screenshots) if r.screenshots else 0,
            "answer": r.answer_excerpt, "docId": r.doc_id, "resolved": r.resolved,
            "createdAt": r.created_at.isoformat() if r.created_at else None,
        } for r in rows],
    }
