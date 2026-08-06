"""M44.P9.12 · Per-user rate limiter.

Redis-backed fixed-window counters keyed by (user_email, action). Returns
HTTP 429 with Retry-After when over the configured limit for that action.

Why per-user, not per-tenant: plan_limits.py already gates per-tenant
LLM spend at the policy level. This adds a tighter, per-user gate to
catch:

  · An automated script slamming the API
  · A reviewer accidentally clicking 'Re-extract' 50 times
  · One bad actor in a multi-reviewer tenant using up the budget

Action-specific limits stay close to actual reasonable human pace; an
admin can override them per-tenant in routing_config later if needed.

Usage from a router:

  from app.rate_limit import rate_limit
  @router.post("/path")
  def handler(..., user: CurrentUser = Depends(get_current_user)):
      rate_limit(user.email, action="chat_msg")
      ...

The check is async-safe via redis-py 5's connection pool. Failure to
reach Redis fails OPEN (allows the call) · we'd rather not break the
app on transient Redis hiccups.
"""
from __future__ import annotations

import logging
from typing import Final

from fastapi import HTTPException, status

log = logging.getLogger("docaiq.rate_limit")

# ── Per-action limits ────────────────────────────────────────────────────
# (calls_per_window, window_seconds). Calibrated for actual human pace,
# not "as fast as the API can handle". A real user typing at 60 wpm
# writes ~12 char/sec · 5 chat messages per minute is a brisk rapid
# back-and-forth. We allow ~3x that as headroom for sub-second
# clicking between turns.
_LIMITS: Final[dict[str, tuple[int, int]]] = {
    "chat_msg":      (30,  60),    # 30 chat messages per 60 s
    "doc_upload":    (20, 300),    # 20 uploads per 5 min
    "doc_reclassify": (10, 60),    # 10 reclassifies per minute
    "field_edit":    (60,  60),    # 60 field edits per minute
    "agent_run":     (20,  60),    # 20 agent invocations per minute
    "register":      (5, 3600),    # M48 · 5 sign-ups per hour per client IP (abuse guard)
    "login":         (10, 60),     # 10 password attempts/min per client IP (brute-force guard)
    "default":       (200, 60),    # generic catch-all
}

# Human-friendly labels so the 429 reads as "you're going too fast", not "rate limit ·
# 30 chat_msg per 60s" (which users mistake for an AI/LLM limit).
_FRIENDLY: Final[dict[str, str]] = {
    "chat_msg":       "You're sending chat messages too quickly",
    "doc_upload":     "You're uploading documents too quickly",
    "doc_reclassify": "You're re-classifying too quickly",
    "field_edit":     "You're editing fields too quickly",
    "agent_run":      "Too many AI runs in a row",
    "register":       "Too many sign-up attempts",
    "login":          "Too many login attempts",
}


def rate_limit(user_email: str, *, action: str = "default") -> None:
    """Increment the counter for this (user, action) and 429 if over.

    Each window is `window_seconds` long, starts on the first call,
    ends + counter resets on natural Redis TTL.

    Fail-open: any Redis exception logs at WARNING and allows the call.
    Better to serve a few extra requests during a Redis blip than 503
    the whole tenant.
    """
    limit, window = _LIMITS.get(action, _LIMITS["default"])
    if limit <= 0:
        return  # disabled action

    try:
        import redis
        from app.config import get_settings
        s = get_settings()
        # Sync redis client · matches the rest of our request-path code
        client = redis.Redis.from_url(s.redis_url, decode_responses=True)
        key = f"docaiq:rl:{action}:{user_email}"
        try:
            # INCR + EXPIRE pattern · O(1), atomic enough for our needs
            # (a tiny window where the first INCR succeeds before EXPIRE
            # exists; not a problem in practice).
            count = client.incr(key)
            if count == 1:
                client.expire(key, window)
            elif count > limit:
                ttl = client.ttl(key)
                retry_after = max(1, int(ttl))
                log.info(
                    "rate-limit: user=%s action=%s count=%d/%d · 429 (retry in %ds)",
                    user_email, action, count, limit, retry_after,
                )
                friendly = _FRIENDLY.get(action, "You're doing that too quickly")
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail={
                        "code": "rate_limited",
                        "message": f"{friendly} — please wait ~{retry_after}s and try again.",
                    },
                    headers={"Retry-After": str(retry_after)},
                )
        finally:
            client.close()
    except HTTPException:
        raise  # 429 must propagate
    except Exception as e:  # noqa: BLE001
        # Any Redis / config error · fail open
        log.warning("rate-limit check failed (allowing call): %s", e)
