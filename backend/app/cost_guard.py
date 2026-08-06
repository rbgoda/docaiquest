"""LLM spend guards — protect the budget under a testing flood.

Two ceilings, both configurable and default-OFF (set the env vars to enable):
  · documents_daily_llm_cap        — total LLM calls / UTC day  (budget kill-switch)
  · documents_user_hourly_llm_cap  — per signed-in user / hour  (fairness + runaway)

Enforced at the gateway (the single LLM chokepoint). Best-effort: a Redis
outage never blocks a call. On a hit it raises CostCapExceeded; gateway callers
(route / llm_one_shot) already degrade gracefully (chat shows "temporarily
unavailable", extraction returns no_extraction).
"""
from __future__ import annotations

import logging
import time

from app.config import get_settings

log = logging.getLogger("docaiq.cost_guard")


class CostCapExceeded(Exception):
    """Raised when a daily/hourly LLM ceiling is hit."""


def _redis():
    import redis as _r  # redis-py (pulled in by arq)
    return _r.Redis.from_url(get_settings().redis_url, socket_connect_timeout=1, socket_timeout=1)


# Small in-process cache (owner_pk → plan) so the per-plan cap lookup doesn't hit the DB on every
# LLM call. 5-minute TTL — plan changes take effect within minutes.
_PLAN_CACHE: dict[int, tuple[str | None, float]] = {}


def _owner_plan(owner_pk: int) -> str | None:
    now = time.time()
    hit = _PLAN_CACHE.get(owner_pk)
    if hit and hit[1] > now:
        return hit[0]
    plan = None
    try:
        from app.db import SessionLocal
        from app.orm import User
        from app.services.subscriptions import effective_plan
        with SessionLocal() as s:
            u = s.get(User, owner_pk)
            plan = effective_plan(u) if u else None
    except Exception:  # noqa: BLE001 — never block on the lookup
        plan = None
    _PLAN_CACHE[owner_pk] = (plan, now + 300)
    return plan


def guard(tenant_id: str | None, owner_pk: int | None = None) -> None:
    s = get_settings()
    daily = getattr(s, "documents_daily_llm_cap", 0) or 0
    hourly = getattr(s, "documents_user_hourly_llm_cap", 0) or 0
    if daily <= 0 and hourly <= 0:
        return
    try:
        r = _redis()
        now = time.time()
        if daily > 0 and tenant_id:
            k = f"llmcap:day:{tenant_id}:{int(now // 86400)}"
            n = r.incr(k)
            if n == 1:
                r.expire(k, 90_000)
            if n > daily:
                log.warning("daily LLM cap hit · tenant=%s n=%s cap=%s", tenant_id, n, daily)
                raise CostCapExceeded("daily LLM budget reached")
        if hourly > 0 and owner_pk:
            # Enterprise-plan users get the higher hourly ceiling.
            if _owner_plan(owner_pk) == "enterprise":
                hourly = max(hourly, getattr(s, "documents_enterprise_hourly_llm_cap", 0) or hourly)
            k = f"llmcap:hr:{tenant_id}:{owner_pk}:{int(now // 3600)}"
            n = r.incr(k)
            if n == 1:
                r.expire(k, 4_000)
            if n > hourly:
                log.warning("hourly per-user LLM cap hit · owner=%s n=%s cap=%s", owner_pk, n, hourly)
                raise CostCapExceeded("hourly LLM rate limit reached")
    except CostCapExceeded:
        raise
    except Exception as e:  # noqa: BLE001 — never block on a limiter outage
        log.debug("cost_guard unavailable: %s", e)
