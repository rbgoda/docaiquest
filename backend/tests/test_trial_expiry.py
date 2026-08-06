"""subscriptions.effective_plan — the trial branch (expired trial → free).

The headline plan-resolution rule; only the promo branch was covered before
(test_promo_expiry). Pure, no DB — a fake User like test_promo_expiry's _U.
"""
from __future__ import annotations

import datetime as dt

from app.services.subscriptions import TRIAL_DAYS, effective_plan, trial_days_left


class _U:
    def __init__(self, **kw):
        self.plan = kw.get("plan", "trial")
        self.trial_ends_at = kw.get("trial_ends_at")
        self.created_at = kw.get("created_at")
        self.plan_expires_at = kw.get("plan_expires_at")


def _now():
    return dt.datetime.now(dt.timezone.utc)


def test_active_trial_stays_trial():
    assert effective_plan(_U(plan="trial", trial_ends_at=_now() + dt.timedelta(days=3))) == "trial"


def test_expired_trial_reverts_to_free():
    assert effective_plan(_U(plan="trial", trial_ends_at=_now() - dt.timedelta(days=1))) == "free"


def test_legacy_trial_anchored_on_signup():
    # trial_ends_at NULL → window anchored on created_at (TRIAL_DAYS)
    old = _now() - dt.timedelta(days=TRIAL_DAYS + 1)
    assert effective_plan(_U(plan="trial", trial_ends_at=None, created_at=old)) == "free"
    recent = _now() - dt.timedelta(days=3)
    assert effective_plan(_U(plan="trial", trial_ends_at=None, created_at=recent)) == "trial"


def test_none_plan_treated_as_trial():
    assert effective_plan(_U(plan=None, trial_ends_at=_now() + dt.timedelta(days=1))) == "trial"


def test_trial_days_left():
    assert trial_days_left(_U(plan="free")) is None          # non-trial → None
    assert trial_days_left(_U(plan="pro")) is None
    left = trial_days_left(_U(plan="trial", trial_ends_at=_now() + dt.timedelta(days=3, hours=2)))
    assert left is not None and 3 <= left <= 4
    assert trial_days_left(_U(plan="trial", trial_ends_at=_now() - dt.timedelta(days=1))) == 0
