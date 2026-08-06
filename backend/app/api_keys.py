"""API key generation + hashing for third-party clients (v1 API/SDK).

A key looks like `dq_live_<32 url-safe chars>` (or `dq_test_…`). We store only
its SHA-256 hash + a short prefix for display; the raw key is shown once at
creation and never persisted. See docs/SDK_AND_API_DESIGN.md §4.
"""
from __future__ import annotations

import hashlib
import secrets

_PREFIX_LEN = 16  # chars of the raw key kept for display (incl. the dq_<env>_ part)


def generate_key(env: str = "live") -> str:
    """Mint a new raw API key. Shown to the partner ONCE."""
    env = "test" if env == "test" else "live"
    return f"dq_{env}_{secrets.token_urlsafe(24)}"


def hash_key(raw: str) -> str:
    """Stable SHA-256 of the raw key — what we store + look up by."""
    return hashlib.sha256((raw or "").encode("utf-8")).hexdigest()


def key_prefix(raw: str) -> str:
    """Short, non-secret display prefix, e.g. 'dq_live_AbCdEf…'."""
    return (raw or "")[:_PREFIX_LEN] + "…"


def parse_env(raw: str) -> str:
    """'live' / 'test' from a key, defaulting to 'live'."""
    return "test" if (raw or "").startswith("dq_test_") else "live"
