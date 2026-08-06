"""Plan-usage summary endpoint · powers the in-app upgrade banner for free
tenants. Always mounted (returns {planType:"paid"} for non-free tenants).

Extracted from main.py to follow the 1-router-per-file convention.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.db import get_session

router = APIRouter()


@router.get("/usage")
def get_usage(db=Depends(get_session)) -> dict:
    from app.plan_limits import get_usage_summary
    return get_usage_summary(db)
