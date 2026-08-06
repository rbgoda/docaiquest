"""Auth primitives: password hashing, our own session JWT, Google ID-token verify.

Design notes
------------

* **One cookie format.** Whether the user logged in via dev mode (email+password)
  or Google OIDC, the session cookie is always a JWT *we* issue, signed HS256
  with `DOCAIQ_JWT_SECRET`. Tenant resolution then has one path: read `org_id`
  from our cookie.

* **Google is an identity provider, not a session source.** After a Google
  callback we validate Google's ID token, look up the user by email in the
  current tenant, and only *then* issue our own JWT. Unknown emails are
  rejected — the allowlist is enforced by the `users` table.

* **OIDC-ready.** `verify_oidc_id_token` works for any RS256 JWT with a JWKS
  URL — Google today, WorkOS/Auth0/Okta tomorrow via DOCAIQ_OIDC_* env vars.
"""

from __future__ import annotations

import time
from functools import lru_cache
from typing import Any

import httpx
import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from app.config import get_settings


# ---- Passwords (dev-mode only) -----------------------------------------------
# DUPLICATION CONTRACT (TODO #34) — `_hasher`, `hash_password`,
# `verify_password` MUST stay byte-identical with control_plane/app/auth.py.
# Divergence silently breaks cross-side hash verification. Proper fix is a
# shared `libs/docaiq_core/` package; until then the cross-tenant pytest
# enforces drift detection.
_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        _hasher.verify(password_hash, password)
        return True
    except VerifyMismatchError:
        return False
    except Exception:
        return False


# A fixed dummy argon2 hash to verify against on the login MISS path, so a
# non-existent (or password-less / OAuth-only) account takes the same argon2 time
# as a real one — closes the user-enumeration timing oracle at login.
_DUMMY_HASH = _hasher.hash("constant-time-login-dummy-password")


def password_ok(password: str, password_hash: str | None) -> bool:
    """Verify a login password in ~constant time regardless of whether the account
    exists. On a miss (no account / no stored hash) still run a full argon2 verify
    against a dummy hash so response timing doesn't reveal registered emails."""
    if password_hash:
        return verify_password(password, password_hash)
    verify_password(password, _DUMMY_HASH)   # burn equivalent work; result ignored
    return False


# ---- Session JWT (the cookie we issue) --------------------------------------
def issue_session_token(
    *,
    user_id: int,
    email: str,
    name: str,
    org_id: str,
    roles: list[str],
    vendor_pk: int | None = None,
    token_version: int = 0,
) -> str:
    settings = get_settings()
    now = int(time.time())
    claims = {
        "iss": "docaiq",
        "sub": str(user_id),
        "email": email,
        "name": name,
        "org_id": org_id,
        "roles": roles,
        # Session revocation — the user's token_version at issue time; a later bump
        # (logout-all / password change / freeze) makes this token stale. Only
        # enforced when settings.session_revocation is on.
        "tv": token_version,
        # M17 phase 3 — for vendor-only users, bind the JWT to a specific
        # Vendor row. Repositories filter by this when present so a vendor
        # user never sees another vendor's data even if they manage to
        # call an endpoint that's role-gated only to "vendor".
        "vendor_pk": vendor_pk,
        "iat": now,
        "exp": now + settings.jwt_ttl_seconds,
    }
    return jwt.encode(claims, settings.jwt_secret, algorithm="HS256")


def verify_session_token(token: str) -> dict[str, Any]:
    """Decode and validate one of our own HS256 session tokens.
    Raises `jwt.PyJWTError` on any failure (expired, bad sig, malformed)."""
    settings = get_settings()
    return jwt.decode(
        token,
        settings.jwt_secret,
        algorithms=["HS256"],
        issuer="docaiq",
        # `email` is required: the superadmin gate authorizes on it, so a token
        # without it must never validate (defence-in-depth alongside the DB
        # re-read in require_superadmin).
        options={"require": ["exp", "iat", "sub", "org_id", "iss", "email"]},
    )


# M48 · short-lived, single-purpose tokens for email verification. Signed with
# the same HS256 secret but scoped by a `purpose` claim so a session token can
# never be used to verify (and vice-versa).
def issue_email_token(email: str, *, purpose: str = "verify_email", ttl_seconds: int = 86400) -> str:
    settings = get_settings()
    now = int(time.time())
    claims = {"iss": "docaiq", "purpose": purpose, "email": email.lower(),
              "iat": now, "exp": now + ttl_seconds}
    return jwt.encode(claims, settings.jwt_secret, algorithm="HS256")


def verify_email_token(token: str, *, purpose: str = "verify_email") -> str:
    """Return the email a valid token confirms, or raise jwt.PyJWTError."""
    settings = get_settings()
    claims = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"],
                        issuer="docaiq",
                        options={"require": ["exp", "iat", "email", "purpose", "iss"]})
    if claims.get("purpose") != purpose:
        raise jwt.InvalidTokenError("wrong token purpose")
    return claims["email"]


# ---- External OIDC ID-token verify (Google today, others later) -------------
GOOGLE_JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
GOOGLE_ISSUERS = ("https://accounts.google.com", "accounts.google.com")


@lru_cache(maxsize=4)
def _jwks_client(jwks_url: str) -> jwt.PyJWKClient:
    """Cached JWKS client — pyjwt's client caches keys internally and refreshes
    on KID misses, so this is the right primitive."""
    return jwt.PyJWKClient(jwks_url, cache_keys=True)


def verify_google_id_token(id_token: str) -> dict[str, Any]:
    """Validate a Google-issued OIDC ID token against the configured Client ID.
    Returns the decoded claims. Raises `jwt.PyJWTError` on any failure."""
    settings = get_settings()
    if not settings.google_client_id:
        raise jwt.PyJWTError("Google OIDC not configured (DOCAIQ_GOOGLE_CLIENT_ID empty)")

    signing_key = _jwks_client(GOOGLE_JWKS_URL).get_signing_key_from_jwt(id_token).key
    claims = jwt.decode(
        id_token,
        signing_key,
        algorithms=["RS256"],
        audience=settings.google_client_id,
        options={"require": ["exp", "iat", "iss", "sub", "email"]},
    )
    if claims.get("iss") not in GOOGLE_ISSUERS:
        raise jwt.PyJWTError(f"Unexpected issuer: {claims.get('iss')!r}")
    if not claims.get("email_verified", False):
        raise jwt.PyJWTError("Google email not verified")
    return claims


# ---- Google OAuth code exchange ---------------------------------------------
GOOGLE_AUTHZ_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"


def google_authz_url(redirect_uri: str, state: str) -> str:
    """Build the URL we 302 the browser to for Google sign-in."""
    settings = get_settings()
    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        # `prompt=select_account` lets users pick if they have multiple Google sessions.
        "prompt": "select_account",
        "access_type": "online",
    }
    return f"{GOOGLE_AUTHZ_URL}?{httpx.QueryParams(params)}"


def exchange_google_code(code: str, redirect_uri: str) -> str:
    """Exchange an authz code for an ID token. Returns the raw ID token string;
    callers should then run it through `verify_google_id_token`."""
    settings = get_settings()
    if not (settings.google_client_id and settings.google_client_secret):
        raise RuntimeError("Google OIDC not fully configured")
    response = httpx.post(
        GOOGLE_TOKEN_URL,
        data={
            "code": code,
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        },
        timeout=10,
    )
    response.raise_for_status()
    payload = response.json()
    if "id_token" not in payload:
        raise RuntimeError(f"Google token endpoint returned no id_token: {payload}")
    return payload["id_token"]
