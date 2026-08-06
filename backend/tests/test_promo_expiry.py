"""Promo-granted plans expire back to free (pure effective_plan test)."""
from __future__ import annotations
import datetime as dt
from app.services.subscriptions import effective_plan


class _U:
    def __init__(self, **kw):
        self.plan = kw.get("plan", "pro")
        self.trial_ends_at = None
        self.created_at = None
        self.plan_expires_at = kw.get("plan_expires_at")


def test_promo_plan_reverts_to_free_after_expiry():
    now = dt.datetime.now(dt.timezone.utc)
    assert effective_plan(_U(plan="pro", plan_expires_at=None)) == "pro"               # no expiry
    assert effective_plan(_U(plan="pro", plan_expires_at=now + dt.timedelta(days=5))) == "pro"   # future
    assert effective_plan(_U(plan="pro", plan_expires_at=now - dt.timedelta(days=1))) == "free"  # past
    assert effective_plan(_U(plan="enterprise", plan_expires_at=now - dt.timedelta(days=1))) == "free"
