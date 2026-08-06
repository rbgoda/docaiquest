"""Resolve + authorize third-party API callers (v1 API/SDK).

`require_client(*scopes)` is a FastAPI dependency that:
  · reads the key from `Authorization: Bearer <key>` or `X-API-Key`,
  · accepts the legacy `DOCAIQ_EXTRACTION_API_KEY` as an implicit all-scope
    client (back-compat for AuditAIQ during migration),
  · else hashes the key, looks it up in `api_clients`, rejects revoked keys,
  · enforces required scopes + a per-key RPM rate limit (Redis, best-effort),
  · stamps `last_used_at` and sets the tenant ContextVar.

It is ASYNC on purpose: a sync `def` dependency runs in a threadpool, where
ContextVar writes don't propagate to the route (see CLAUDE.md). Async keeps the
tenant context on the same task as the handler.
"""
from __future__ import annotations

import logging
import secrets
import time
from datetime import datetime, timezone

from fastapi import Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import api_keys
from app.config import get_settings
from app.db import get_session, set_current_tenant
from app.orm import ApiClient

log = logging.getLogger("docaiq.api_clients")

LEGACY_NAME = "legacy-extraction-key"


class Caller:
    """The resolved API caller — a real ApiClient row, or the legacy env key."""

    def __init__(self, *, pk: int | None, name: str, scopes: list[str],
                 legacy: bool = False, allowed_group_ids: list[int] | None = None,
                 owner_user_id: int | None = None):
        self.pk = pk
        self.name = name
        self.scopes = scopes
        self.legacy = legacy
        self.allowed_group_ids = allowed_group_ids or []
        self.owner_user_id = owner_user_id

    def may_access_group(self, group_id: int) -> bool:
        """Legacy all-access key, or the group is explicitly granted to this key."""
        return self.legacy or group_id in (self.allowed_group_ids or [])


def _extract_key(authorization: str | None, x_api_key: str | None) -> str | None:
    if authorization and authorization[:7].lower() == "bearer ":
        return authorization[7:].strip()
    if x_api_key:
        return x_api_key.strip()
    return None


def _rate_limit(key_id: str, rpm: int) -> None:
    """Fixed-window per-key RPM via Redis. Best-effort: never block a request on
    a limiter outage (just log)."""
    if rpm <= 0:
        return
    try:
        import redis as _redis  # redis-py (pulled in by arq)
        r = _redis.Redis.from_url(get_settings().redis_url,
                                  socket_connect_timeout=1, socket_timeout=1)
        bucket = f"apirl:{key_id}:{int(time.time() // 60)}"
        n = r.incr(bucket)
        if n == 1:
            r.expire(bucket, 70)
        if n > rpm:
            raise HTTPException(status_code=429, detail="rate limit exceeded",
                                headers={"Retry-After": "60"})
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001 — best-effort
        log.debug("api rate limiter unavailable: %s", e)


def require_client(*required_scopes: str):
    """Dependency factory. `Depends(require_client("extract"))` → Caller."""

    async def dep(
        authorization: str | None = Header(default=None),
        x_api_key: str | None = Header(default=None),
        db: Session = Depends(get_session),
    ) -> Caller:
        settings = get_settings()
        raw = _extract_key(authorization, x_api_key)
        if not raw:
            raise HTTPException(status_code=401,
                                detail="missing API key (Authorization: Bearer <key> or X-API-Key)")

        # Back-compat: the single legacy extraction key = implicit all-scope client. Bound it with a
        # rate limit too (it previously skipped the limiter entirely) — full de-scoping is a partner
        # migration (move AuditAIQ to a revocable DB key), tracked separately.
        legacy = settings.extraction_api_key
        if legacy and secrets.compare_digest(raw, legacy):
            _rate_limit("legacy", 300)
            set_current_tenant(settings.tenant_id)
            return Caller(pk=None, name=LEGACY_NAME, scopes=["*"], legacy=True)

        client = db.scalar(select(ApiClient).where(ApiClient.key_hash == api_keys.hash_key(raw)))
        if client is None or client.revoked_at is not None:
            raise HTTPException(status_code=401, detail="invalid or revoked API key")

        granted = set(client.scopes or [])
        if required_scopes and "*" not in granted and not set(required_scopes).issubset(granted):
            missing = sorted(set(required_scopes) - granted)
            raise HTTPException(status_code=403, detail=f"API key missing scope(s): {missing}")

        _rate_limit(str(client.pk), client.rate_limit_rpm or 0)
        set_current_tenant(client.tenant_id)
        # Enterprise self-serve key → scope every downstream query to that user's own documents,
        # exactly like a logged-in session. Partner/admin keys leave owner_user_id NULL (tenant-wide).
        if client.owner_user_id is not None:
            from app.documents_scope import set_current_owner_user_pk
            set_current_owner_user_pk(client.owner_user_id)
        client.last_used_at = datetime.now(timezone.utc)
        try:
            db.commit()
        except Exception:  # noqa: BLE001
            db.rollback()
        return Caller(pk=client.pk, name=client.name, scopes=sorted(granted), legacy=False,
                      allowed_group_ids=list(client.allowed_group_ids or []),
                      owner_user_id=client.owner_user_id)

    return dep
