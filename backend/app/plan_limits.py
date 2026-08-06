"""M36/M37 · plan-limit gates for the shared SaaS container.

The shared container hosts many free-tier tenants. Each one is capped on
documents per month, audits, and LLM calls per hour. This module centralizes
the cap-check + counter-increment logic so routers stay readable.

Caps for plan_type='free' (matches docs/SAAS_VS_CONTAINER_PLAN.md):
  * 50 documents per calendar month
  * 1 audit ever (free is for evaluation · upgrade to subscribe)
  * 5 requirements per audit
  * 5 LLM calls per rolling hour

For plan_type='paid' (per-tenant containers · the existing model) every
check is a no-op so this module adds zero overhead.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_current_tenant
from app.orm import Tenant


# Free-tier caps. Adjust here if pricing changes.
FREE_DOC_MONTHLY = 50
FREE_AUDIT_CAP = 1
FREE_AUDIT_REQUIREMENT_CAP = 5
FREE_LLM_PER_HOUR = 5

_UPGRADE_HINT = "Upgrade to a paid plan for a dedicated workspace with higher limits."


def _get_tenant(db: Session) -> Tenant | None:
    tid = get_current_tenant()
    if not tid:
        return None
    return db.scalar(select(Tenant).where(Tenant.id == tid))


def is_free_tenant(db: Session) -> bool:
    """True for plan_type='free' tenants. Used by the LLM router to lock the
    cascade to tier 1 (cheap/free models) so a free tenant never escalates to
    paid tiers on the shared key."""
    t = _get_tenant(db)
    return bool(t and t.plan_type == "free")


def check_can_upload_document(db: Session) -> None:
    """Raise 402 if a free tenant has hit the monthly document cap."""
    t = _get_tenant(db)
    if t is None or t.plan_type != "free":
        return
    if (t.doc_count_this_month or 0) >= FREE_DOC_MONTHLY:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=f"Free plan limit reached: {FREE_DOC_MONTHLY} documents/month. {_UPGRADE_HINT}",
        )


def record_document_uploaded(db: Session) -> None:
    """Increment the monthly doc counter. Caller commits."""
    t = _get_tenant(db)
    if t is None or t.plan_type != "free":
        return
    t.doc_count_this_month = (t.doc_count_this_month or 0) + 1


def check_can_create_audit(db: Session, requirement_count: int = 0) -> None:
    """Raise 402 if free tenant already has an audit OR exceeds req cap."""
    t = _get_tenant(db)
    if t is None or t.plan_type != "free":
        return
    if (t.audits_created or 0) >= FREE_AUDIT_CAP:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=f"Free plan limit reached: {FREE_AUDIT_CAP} audit. {_UPGRADE_HINT}",
        )
    if requirement_count > FREE_AUDIT_REQUIREMENT_CAP:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=(
                f"Free plan: max {FREE_AUDIT_REQUIREMENT_CAP} requirements per audit "
                f"(got {requirement_count}). {_UPGRADE_HINT}"
            ),
        )


def record_audit_created(db: Session) -> None:
    """Increment the lifetime audit counter. Caller commits."""
    t = _get_tenant(db)
    if t is None or t.plan_type != "free":
        return
    t.audits_created = (t.audits_created or 0) + 1


def check_and_record_llm_call(db: Session) -> None:
    """Rolling-hour rate limit on free tier. Raises 429 when over.

    Window opens at first call after the hour passes. Counter increments
    on every call. Caller commits the session.
    """
    t = _get_tenant(db)
    if t is None or t.plan_type != "free":
        return
    now = datetime.now(timezone.utc)
    if t.llm_hour_window_start is None or (now - t.llm_hour_window_start) > timedelta(hours=1):
        t.llm_hour_window_start = now
        t.llm_calls_this_hour = 1
        return
    if (t.llm_calls_this_hour or 0) >= FREE_LLM_PER_HOUR:
        # Compute "try again in N min" for the error body.
        retry_after = max(1, 60 - int((now - t.llm_hour_window_start).total_seconds() // 60))
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Free plan LLM rate limit: {FREE_LLM_PER_HOUR} calls/hour. Try again in ~{retry_after} min. {_UPGRADE_HINT}",
        )
    t.llm_calls_this_hour = (t.llm_calls_this_hour or 0) + 1


def get_usage_summary(db: Session) -> dict:
    """Surface counters + caps for the in-app upgrade banner."""
    t = _get_tenant(db)
    if t is None:
        return {"planType": "paid", "limits": None}
    if t.plan_type != "free":
        return {"planType": t.plan_type, "limits": None}
    from app.config import get_settings
    return {
        "planType": "free",
        "upgradeUrl": get_settings().upgrade_url,
        "limits": {
            "documents": {"used": t.doc_count_this_month or 0, "cap": FREE_DOC_MONTHLY, "window": "month"},
            "audits": {"used": t.audits_created or 0, "cap": FREE_AUDIT_CAP, "window": "lifetime"},
            "llmCallsPerHour": {"used": t.llm_calls_this_hour or 0, "cap": FREE_LLM_PER_HOUR, "window": "hour"},
            "requirementsPerAudit": {"cap": FREE_AUDIT_REQUIREMENT_CAP},
        },
        "hourWindowStart": t.llm_hour_window_start.isoformat() if t.llm_hour_window_start else None,
    }
