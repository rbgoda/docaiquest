"""Unit tests for the LLM spend guard (caps off = no-op; over-cap raises)."""
from __future__ import annotations

from app import cost_guard


def test_caps_off_is_noop():
    # Default config has both caps = 0 → returns without touching Redis.
    assert cost_guard.guard("tenant", 1) is None
    assert cost_guard.guard("tenant", None) is None


def test_daily_cap_raises_when_exceeded():
    class _Settings:
        documents_daily_llm_cap = 2
        documents_user_hourly_llm_cap = 0
        redis_url = "redis://unused"

    class _Redis:
        def __init__(self):
            self.store = {}
        def incr(self, k):
            self.store[k] = self.store.get(k, 0) + 1
            return self.store[k]
        def expire(self, k, ttl):
            pass

    orig_settings, orig_redis = cost_guard.get_settings, cost_guard._redis
    fake = _Redis()
    cost_guard.get_settings = lambda: _Settings()
    cost_guard._redis = lambda: fake
    try:
        cost_guard.guard("t", None)   # 1 — ok
        cost_guard.guard("t", None)   # 2 — ok (== cap)
        raised = False
        try:
            cost_guard.guard("t", None)   # 3 — over cap
        except cost_guard.CostCapExceeded:
            raised = True
        assert raised
    finally:
        cost_guard.get_settings, cost_guard._redis = orig_settings, orig_redis


def test_per_user_hourly_cap_raises():
    class _Settings:
        documents_daily_llm_cap = 0
        documents_user_hourly_llm_cap = 1
        redis_url = "redis://unused"

    class _Redis:
        def __init__(self):
            self.store = {}
        def incr(self, k):
            self.store[k] = self.store.get(k, 0) + 1
            return self.store[k]
        def expire(self, k, ttl):
            pass

    orig_settings, orig_redis = cost_guard.get_settings, cost_guard._redis
    fake = _Redis()
    cost_guard.get_settings = lambda: _Settings()
    cost_guard._redis = lambda: fake
    try:
        cost_guard.guard("t", 99)     # 1 — ok (== cap)
        raised = False
        try:
            cost_guard.guard("t", 99)  # 2 — over
        except cost_guard.CostCapExceeded:
            raised = True
        assert raised
        # a DIFFERENT user is independent
        cost_guard.guard("t", 100)
    finally:
        cost_guard.get_settings, cost_guard._redis = orig_settings, orig_redis
