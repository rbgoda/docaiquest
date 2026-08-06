"""Launch-safety hardening (feat/launch-safety-hardening).

Covers four independent fixes:
  1. verify_session_token pins issuer + requires the `email` claim.
  2. Embedding-dim boot probe (assert_embed_dim) fails fast on a native≠target
     mismatch instead of silently truncating/padding.
  3. require_superadmin authorizes on the DB email (re-read), not the JWT claim.
  4. /auth/login is rate-limited (per-IP brute-force guard).

All offline — no Postgres/Redis/network required.
"""
from __future__ import annotations

import time
from types import SimpleNamespace

import jwt
import pytest


# ── 1 · session-token validation ────────────────────────────────────────────

def _secret() -> str:
    from app.config import get_settings
    return get_settings().jwt_secret


def _mint(extra: dict | None = None, *, drop: tuple[str, ...] = ()) -> str:
    now = int(time.time())
    claims = {
        "iss": "docaiq", "sub": "1", "email": "u@x.io", "name": "U",
        "org_id": "t", "roles": ["admin"], "iat": now, "exp": now + 3600,
    }
    claims.update(extra or {})
    for k in drop:
        claims.pop(k, None)
    return jwt.encode(claims, _secret(), algorithm="HS256")


def test_valid_token_decodes():
    from app.auth import verify_session_token
    claims = verify_session_token(_mint())
    assert claims["email"] == "u@x.io"
    assert claims["iss"] == "docaiq"


def test_token_missing_issuer_rejected():
    from app.auth import verify_session_token
    with pytest.raises(jwt.PyJWTError):
        verify_session_token(_mint(drop=("iss",)))


def test_token_wrong_issuer_rejected():
    from app.auth import verify_session_token
    with pytest.raises(jwt.InvalidIssuerError):
        verify_session_token(_mint({"iss": "evil"}))


def test_token_missing_email_rejected():
    """The superadmin gate authorizes on email — a token without it must never
    validate."""
    from app.auth import verify_session_token
    with pytest.raises(jwt.PyJWTError):
        verify_session_token(_mint(drop=("email",)))


# ── 2 · embedding-dim boot probe ─────────────────────────────────────────────

def test_coerce_dim_truncates_pads_and_records_native():
    from app import embeddings as e
    assert e._coerce_dim([[1.0, 2.0, 3.0]], 2, backend="x") == [[1.0, 2.0]]
    assert e._last_native_dim == 3
    assert e._coerce_dim([[1.0]], 3, backend="x") == [[1.0, 0.0, 0.0]]
    assert e._last_native_dim == 1
    assert e._coerce_dim([[1.0, 2.0]], 2, backend="x") == [[1.0, 2.0]]


def test_assert_embed_dim_hash_backend_is_noop(monkeypatch):
    from app import embeddings as e
    monkeypatch.setattr(e, "get_settings",
                        lambda: SimpleNamespace(embed_backend="hash", embed_dim=384))
    e.assert_embed_dim()  # must not raise


def test_assert_embed_dim_raises_on_mismatch(monkeypatch):
    from app import embeddings as e
    monkeypatch.setattr(e, "get_settings",
                        lambda: SimpleNamespace(embed_backend="dashscope", embed_dim=384))
    # Fake a backend that natively emits 1024d (the real dashscope footgun).
    monkeypatch.setattr(e, "embed",
                        lambda texts: e._coerce_dim([[0.0] * 1024], 384, backend="dashscope"))
    with pytest.raises(RuntimeError, match="dim mismatch"):
        e.assert_embed_dim()


def test_assert_embed_dim_passes_when_native_matches(monkeypatch):
    from app import embeddings as e
    monkeypatch.setattr(e, "get_settings",
                        lambda: SimpleNamespace(embed_backend="local", embed_dim=384))
    monkeypatch.setattr(e, "embed",
                        lambda texts: e._coerce_dim([[0.0] * 384], 384, backend="local"))
    e.assert_embed_dim()  # native == target → no raise


def test_assert_embed_dim_skips_on_transient_error(monkeypatch):
    """A missing key / model-not-installed must not hard-block boot."""
    from app import embeddings as e
    monkeypatch.setattr(e, "get_settings",
                        lambda: SimpleNamespace(embed_backend="openai", embed_dim=384))

    def _boom(texts):
        raise RuntimeError("no api key")

    monkeypatch.setattr(e, "embed", _boom)
    e.assert_embed_dim()  # swallowed → no raise


# ── 3 · superadmin gate re-reads the DB ──────────────────────────────────────

def _fake_db(row):
    # .get → the user row; .scalar → None (DB superadmin-allowlist miss), so
    # authorization falls through to the env allowlist / 403 as intended.
    return SimpleNamespace(get=lambda _model, _pk: row, scalar=lambda *_a, **_k: None)


def _docs_settings():
    return SimpleNamespace(product="documents", superadmin_email_set={"boss@x.io"})


def test_superadmin_allows_real_admin(monkeypatch):
    from app.routers import superadmin as sa
    from app.security import CurrentUser
    monkeypatch.setattr(sa, "get_settings", _docs_settings)
    user = CurrentUser(id=1, email="boss@x.io", name="B", org_id="t", roles=("owner",))
    row = SimpleNamespace(email="boss@x.io", is_frozen=False)
    assert sa.require_superadmin(user=user, db=_fake_db(row)) is user


def test_superadmin_denies_forged_email_claim(monkeypatch):
    """JWT claims a superadmin email, but the DB row says otherwise → denied.
    This is the core of the fix: authorize on the DB, not the claim."""
    from fastapi import HTTPException
    from app.routers import superadmin as sa
    from app.security import CurrentUser
    monkeypatch.setattr(sa, "get_settings", _docs_settings)
    user = CurrentUser(id=1, email="boss@x.io", name="B", org_id="t", roles=("owner",))
    row = SimpleNamespace(email="attacker@evil.io", is_frozen=False, tenant_id="t")
    with pytest.raises(HTTPException) as ei:
        sa.require_superadmin(user=user, db=_fake_db(row))
    assert ei.value.status_code == 403


def test_superadmin_denies_frozen(monkeypatch):
    from fastapi import HTTPException
    from app.routers import superadmin as sa
    from app.security import CurrentUser
    monkeypatch.setattr(sa, "get_settings", _docs_settings)
    user = CurrentUser(id=1, email="boss@x.io", name="B", org_id="t", roles=("owner",))
    row = SimpleNamespace(email="boss@x.io", is_frozen=True, tenant_id="t")
    with pytest.raises(HTTPException) as ei:
        sa.require_superadmin(user=user, db=_fake_db(row))
    assert ei.value.status_code == 403


def test_superadmin_denies_missing_user(monkeypatch):
    from fastapi import HTTPException
    from app.routers import superadmin as sa
    from app.security import CurrentUser
    monkeypatch.setattr(sa, "get_settings", _docs_settings)
    user = CurrentUser(id=99, email="boss@x.io", name="B", org_id="t", roles=("owner",))
    with pytest.raises(HTTPException) as ei:
        sa.require_superadmin(user=user, db=_fake_db(None))
    assert ei.value.status_code == 403


# ── 4 · login rate-limit guard ───────────────────────────────────────────────

def test_login_limit_configured():
    from app.rate_limit import _LIMITS
    assert _LIMITS["login"] == (10, 60)


def test_rate_limit_fails_open_without_redis(monkeypatch):
    """A Redis outage must not 500 the login path — fail open."""
    import app.rate_limit as rl

    class _Boom:
        @staticmethod
        def from_url(*a, **k):
            raise OSError("no redis")

    monkeypatch.setitem(__import__("sys").modules, "redis", SimpleNamespace(Redis=_Boom))
    assert rl.rate_limit("1.2.3.4", action="login") is None
