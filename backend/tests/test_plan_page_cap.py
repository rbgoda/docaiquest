"""M47 · free-plan hardening — the per-document page cap (anti-gaming) + the
7-document active cap. Pure enforcement logic (effective_plan / plan_cfg mocked;
no DB): a free user is capped at 7 pages per document; paid/trial plans are uncapped.
"""
from __future__ import annotations

import pytest

from app.services import subscriptions as s


class _DB:
    """A stand-in DB whose .get(User, id) returns a non-None user object."""
    def get(self, *a, **k):
        return object()


@pytest.fixture()
def free(monkeypatch):
    monkeypatch.setattr(s, "effective_plan", lambda u: "free")
    monkeypatch.setattr(s, "plan_cfg", lambda db, p: s.DEFAULT_PLANS[p])
    return _DB()


def test_default_plan_caps():
    assert s.DEFAULT_PLANS["free"]["docs"] == 7
    assert s.DEFAULT_PLANS["free"]["maxPages"] == 1    # single-page test tier
    for p in ("trial", "pro", "enterprise"):
        assert s.DEFAULT_PLANS[p]["maxPages"] is None   # uncapped


def test_enforce_pages_over_cap_raises_402(free):
    with pytest.raises(Exception) as ei:
        s.enforce_pages(free, owner_user_id=1, pages=2)
    err = ei.value
    assert getattr(err, "status_code", None) == 402
    assert err.detail["code"] == "plan_pages"
    assert err.detail["maxPages"] == 1 and err.detail["pages"] == 2


def test_enforce_pages_at_cap_ok(free):
    s.enforce_pages(free, owner_user_id=1, pages=1)      # single page → allowed


@pytest.mark.parametrize("pages", [None, 0, 1])
def test_enforce_pages_noop_cases(free, pages):
    s.enforce_pages(free, owner_user_id=1, pages=pages)  # no raise (≤ 1 page)


def test_page_cap_for_free_is_1(free):
    assert s.page_cap_for(free, owner_user_id=1) == 1


def test_uncapped_plan_allows_large(monkeypatch):
    monkeypatch.setattr(s, "effective_plan", lambda u: "pro")
    monkeypatch.setattr(s, "plan_cfg", lambda db, p: s.DEFAULT_PLANS[p])
    s.enforce_pages(_DB(), owner_user_id=1, pages=500)   # no raise
    assert s.page_cap_for(_DB(), owner_user_id=1) is None
