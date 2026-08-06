"""AIQ Suite SSO — shared-identity verifier (+ issuer).

Lets docaiq accept the same login as every other `*.jicama.tech` app: a JWT
(`HS256`) signed with the shared `JICAMA_SSO_SECRET`, presented either as the
`jicama_sso` cookie (browser login) or `Authorization: Bearer <jwt>` (API).

Zero-dependency on purpose — verifies the signature over the RECEIVED
`header.payload` bytes (never re-serialises the JSON, which would change the
signature). Matches the chataiq reference verifier exactly (see SSO_VERIFIER.md).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

from app.config import get_settings

SSO_COOKIE = "jicama_sso"


def _secret() -> str:
    return get_settings().jicama_sso_secret or ""


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def verify_sso(token: str | None) -> dict | None:
    """Return claims `{sub, name, iss, iat, exp}` if the suite SSO token is valid,
    else None. Valid = correct HMAC over the received header.payload, has a `sub`,
    and not expired."""
    sec = _secret()
    if not sec or not token or token.count(".") != 2:
        return None
    header, payload, sig = token.split(".")
    expect = _b64e(hmac.new(sec.encode(), f"{header}.{payload}".encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(sig, expect):
        return None
    try:
        claims = json.loads(_b64d(payload))
    except Exception:  # noqa: BLE001
        return None
    if not claims.get("sub") or float(claims.get("exp", 0)) < time.time():
        return None
    return claims


def issue_sso(email: str, name: str = "", *, ttl: int = 7 * 24 * 3600, iss: str = "docaiq") -> str:
    """Mint a suite SSO JWT so other apps accept docaiq's login (bidirectional SSO).
    Empty string when no secret / no email."""
    sec = _secret()
    if not sec or not email:
        return ""
    now = int(time.time())
    header = _b64e(b'{"alg":"HS256","typ":"JWT"}')
    payload = _b64e(json.dumps(
        {"iss": iss, "sub": email, "name": name or "", "iat": now, "exp": now + ttl},
        separators=(",", ":")).encode())
    sig = _b64e(hmac.new(sec.encode(), f"{header}.{payload}".encode(), hashlib.sha256).digest())
    return f"{header}.{payload}.{sig}"
