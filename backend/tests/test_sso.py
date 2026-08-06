"""AIQ Suite SSO verifier — interop with chataiq (SSO_VERIFIER.md test vector)."""
from __future__ import annotations

import importlib

import app.config as _cfg
import app.sso as sso

# The shared test vector from SSO_VERIFIER.md (throwaway secret, exp = year 2100).
TEST_SECRET = "test-secret-do-not-use-in-prod-0000"
TOKEN = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJpc3MiOiJjaGF0YWlxIiwic3ViIjoiZGVtb0BqaWNhbWEudGVjaCIsIm5hbWUiOiJEZW1vIFVzZXIiLCJpYXQiOjE3MDAwMDAwMDAsImV4cCI6NDEwMjQ0NDgwMH0."
    "KB4-Ttsm7p02QYr-dspZExKl9OQsl4lR7S55wqcRnyw"
)


def _with_secret(monkeypatch, secret: str):
    monkeypatch.setenv("DOCAIQ_JICAMA_SSO_SECRET", secret)
    _cfg.get_settings.cache_clear()  # Settings is lru_cached


def test_interop_vector_decodes(monkeypatch):
    _with_secret(monkeypatch, TEST_SECRET)
    claims = sso.verify_sso(TOKEN)
    assert claims == {
        "iss": "chataiq", "sub": "demo@jicama.tech", "name": "Demo User",
        "iat": 1700000000, "exp": 4102444800,
    }


def test_tamper_rejected(monkeypatch):
    _with_secret(monkeypatch, TEST_SECRET)
    bad = TOKEN[:-2] + ("aa" if TOKEN[-2:] != "aa" else "bb")
    assert sso.verify_sso(bad) is None


def test_wrong_secret_rejected(monkeypatch):
    _with_secret(monkeypatch, "some-other-secret")
    assert sso.verify_sso(TOKEN) is None


def test_no_secret_returns_none(monkeypatch):
    _with_secret(monkeypatch, "")
    assert sso.verify_sso(TOKEN) is None


def test_garbage_and_empty(monkeypatch):
    _with_secret(monkeypatch, TEST_SECRET)
    for t in (None, "", "not.a.jwt", "a.b", "x.y.z"):
        assert sso.verify_sso(t) is None


def test_expired_rejected(monkeypatch):
    _with_secret(monkeypatch, TEST_SECRET)
    # round-trip our own issuer with a negative TTL → already expired
    tok = sso.issue_sso("x@jicama.tech", "X", ttl=-10)
    assert sso.verify_sso(tok) is None


def test_issue_then_verify_roundtrip(monkeypatch):
    _with_secret(monkeypatch, TEST_SECRET)
    tok = sso.issue_sso("me@jicama.tech", "Me", iss="docaiq")
    claims = sso.verify_sso(tok)
    assert claims and claims["sub"] == "me@jicama.tech" and claims["iss"] == "docaiq"
