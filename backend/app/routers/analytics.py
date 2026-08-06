"""Customer-facing tenant analytics endpoints.

Strictly tenant-scoped. These power the Dashboard, RoutingAdmin estimate
cards, and the Settings → Audit log timeline. Operators monitoring fleet
metrics will use a separate (future) admin stack instead.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app import analytics
from app.db import get_session
from app.security import CurrentUser, require_role

router = APIRouter()

Window = Literal["24h", "7d", "30d", "all"]


# Admin-only analytics surfaces. The UI hides these via sidebar/tab gating
# for non-admins; we additionally enforce on the API so a non-admin user
# can't curl the raw org-wide spend / routing / posture data. `/activity`
# stays open: the Dashboard renders it for every non-vendor persona, and
# vendor-only users don't reach the Dashboard via the sidebar.
@router.get("/analytics/llm-spend")
def get_llm_spend(
    window: Window = Query("7d"),
    db: Session = Depends(get_session),
    _user: CurrentUser = Depends(require_role("admin")),
) -> dict:
    return analytics.llm_spend(db, window=window)


@router.get("/analytics/routing-stats")
def get_routing_stats(
    window: Window = Query("7d"),
    db: Session = Depends(get_session),
    _user: CurrentUser = Depends(require_role("admin")),
) -> dict:
    return analytics.routing_stats(db, window=window)


@router.get("/analytics/activity")
def get_activity(
    limit: int = Query(20, ge=1, le=200),
    db: Session = Depends(get_session),
) -> list[dict]:
    return analytics.activity_feed(db, limit=limit)


@router.get("/analytics/audit-log")
def get_audit_log(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    event_type: str | None = Query(None),
    db: Session = Depends(get_session),
    _user: CurrentUser = Depends(require_role("admin")),
) -> dict:
    return analytics.audit_log(db, limit=limit, offset=offset, event_type=event_type)


@router.get("/analytics/audit-posture")
def get_audit_posture(
    db: Session = Depends(get_session),
    _user: CurrentUser = Depends(require_role("admin")),
) -> dict:
    return analytics.audit_posture(db)


@router.get("/analytics/reviewers")
def get_reviewer_stats(
    db: Session = Depends(get_session),
    _user: CurrentUser = Depends(require_role("admin")),
) -> dict:
    """Admin monitoring · per-reviewer workload, assignments, throughput."""
    return analytics.reviewer_stats(db)
