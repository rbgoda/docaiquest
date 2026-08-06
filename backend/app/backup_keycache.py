"""Transient cache of a user's derived backup-encryption key (Redis).

When a user enables/unlocks password backup encryption, the scrypt-derived key
is cached here so AUTOMATIC backups can encrypt without re-prompting. The key
lives only in Redis with a TTL (refreshed on each unlock) and is NEVER written
to the database. On expiry/logout, auto-backups pause (we skip rather than
write plaintext) until the user unlocks again. Best-effort: Redis being down
just means we can't auto-encrypt (we skip the backup), never a crash.
"""
from __future__ import annotations

import logging

from app.config import get_settings

log = logging.getLogger("docaiq.backup_keycache")

# 7 days — refreshed every time the user enters their password (enable/unlock).
_TTL = 7 * 24 * 3600


def _redis():
    import redis as _r  # redis-py (pulled in by arq)
    return _r.Redis.from_url(get_settings().redis_url, socket_connect_timeout=1, socket_timeout=1)


def _key(tenant_id: str, owner_user_id: int) -> str:
    return f"bkpkey:{tenant_id}:{owner_user_id}"


def put(tenant_id: str, owner_user_id: int, key_b64: bytes) -> None:
    try:
        _redis().set(_key(tenant_id, owner_user_id), key_b64, ex=_TTL)
    except Exception as e:  # noqa: BLE001
        log.warning("backup_keycache.put failed (non-fatal): %s", e)


def get(tenant_id: str, owner_user_id: int) -> bytes | None:
    try:
        v = _redis().get(_key(tenant_id, owner_user_id))
        return v if v else None
    except Exception as e:  # noqa: BLE001
        log.warning("backup_keycache.get failed (non-fatal): %s", e)
        return None


def clear(tenant_id: str, owner_user_id: int) -> None:
    try:
        _redis().delete(_key(tenant_id, owner_user_id))
    except Exception as e:  # noqa: BLE001
        log.warning("backup_keycache.clear failed (non-fatal): %s", e)
