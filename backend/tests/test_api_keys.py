"""Unit tests for third-party API key helpers + key extraction.

No DB/LLM — fast, CI-friendly. The full require_client flow (DB lookup, scopes,
rate limit) is covered by the live smoke in the PR.
"""
from __future__ import annotations

from app import api_keys
from app.api_clients import _extract_key


def test_generate_key_format_and_env():
    live = api_keys.generate_key("live")
    test = api_keys.generate_key("test")
    assert live.startswith("dq_live_") and len(live) > 20
    assert test.startswith("dq_test_")
    assert api_keys.parse_env(live) == "live"
    assert api_keys.parse_env(test) == "test"
    assert live != api_keys.generate_key("live")  # random


def test_hash_is_stable_and_distinct():
    k = api_keys.generate_key()
    assert api_keys.hash_key(k) == api_keys.hash_key(k)
    assert len(api_keys.hash_key(k)) == 64
    assert api_keys.hash_key(k) != api_keys.hash_key(api_keys.generate_key())


def test_prefix_is_non_secret_and_short():
    k = "dq_live_AbCdEfGhIjKlMnOpQrStUv"
    p = api_keys.key_prefix(k)
    assert p.startswith("dq_live_") and p.endswith("…")
    assert len(p) <= 20
    assert k not in p  # never the full key


def test_extract_key_from_bearer_or_xapikey():
    assert _extract_key("Bearer dq_live_abc", None) == "dq_live_abc"
    assert _extract_key("bearer dq_live_abc", None) == "dq_live_abc"   # case-insensitive scheme
    assert _extract_key(None, "dq_live_xyz") == "dq_live_xyz"
    assert _extract_key("Bearer dq_live_abc", "dq_live_xyz") == "dq_live_abc"  # bearer wins
    assert _extract_key(None, None) is None
    assert _extract_key("Token abc", None) is None  # non-bearer scheme ignored
