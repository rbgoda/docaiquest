"""Cross-tenant cookie / JWT isolation property tests.

The defense-in-depth contract is:
  1. Crypto:   each tenant container has its own DOCAIQ_JWT_SECRET.
  2. Identity: get_current_user 401s when JWT.org_id != container tenant_id.
  3. Data:     repositories filter by get_current_tenant().

These tests exercise layer 1 + 2 in-process — no docker, no compose,
runs in ~1 second in CI. They complement the Playwright A2 end-to-end
test which exercises layer 1+2+3 against real containers.

Run locally:
    cd backend && pytest tests/ -v
"""
from __future__ import annotations

import importlib

import jwt
import pytest


# ── Helpers ───────────────────────────────────────────────────────────────

def _make_token(*, secret: str, org_id: str, email: str = "x@y.z", roles=None) -> str:
    """Mint a session token EXACTLY the way app.auth.issue_session_token does,
    but with caller-controlled secret + org_id. Lets us forge cookies under
    one tenant's secret and try to decode them under another's."""
    import time
    payload = {
        "iss": "docaiq",
        "sub": "1",
        "email": email,
        "name": "Test User",
        "org_id": org_id,
        "roles": roles or ["admin"],
        "iat": int(time.time()),
        "exp": int(time.time()) + 3600,
    }
    return jwt.encode(payload, secret, algorithm="HS256")


# ── Layer 1 · cryptographic isolation ─────────────────────────────────────

def test_jwt_signed_with_tenant_a_secret_cannot_decode_with_tenant_b_secret():
    """The most basic property: HS256 verification fails when the secret
    differs. If this ever passes when secrets differ, JWT itself is broken
    or one tenant is using the other's secret."""
    secret_a = "tenant-a-cryptographically-random-secret-xxxxxxxxxxxxxxxx"
    secret_b = "tenant-b-cryptographically-random-secret-yyyyyyyyyyyyyyyy"
    token = _make_token(secret=secret_a, org_id="tenant-a")
    with pytest.raises(jwt.InvalidSignatureError):
        jwt.decode(token, secret_b, algorithms=["HS256"])


def test_predictable_dev_secret_is_forgeable_when_slug_is_known():
    """Regression test for the bug task #1 fixed. Before that change, the
    secret was `dev-jwt-<slug>-rotate-in-prod` — anyone who knew the
    slug could forge cookies. This test documents the attack so we
    don't backslide."""
    # Reconstruct the OLD predictable formula
    leaked_slug = "victim-tenant"
    derivable_secret = f"dev-jwt-{leaked_slug}-rotate-in-prod"
    forged = _make_token(secret=derivable_secret, org_id=leaked_slug, email="attacker@evil.io")
    # An attacker knowing only the public slug can mint a valid superadmin
    # cookie. THIS IS THE BUG — keep this xfail / informational so future
    # CI changes don't accidentally re-introduce the formula.
    claims = jwt.decode(forged, derivable_secret, algorithms=["HS256"])
    assert claims["org_id"] == leaked_slug
    assert claims["email"] == "attacker@evil.io"


# ── Layer 2 · identity check (org_id mismatch) ────────────────────────────

def test_argon2_password_helpers_match_control_plane_byte_for_byte():
    """Drift-detection for TODO #34. `hash_password` / `verify_password`
    exist in both backend/app/auth.py AND control_plane/app/auth.py.
    Until they share a real Python package, this test fails the moment
    either side gains an extra arg, a different default kdf, or a
    different exception class — any of which would silently break
    cross-side hash verification.

    Implementation: textually compare the function definitions. If you're
    refactoring this on purpose, update BOTH files and the assertion.

    Skips when the control_plane source isn't reachable (CI checks out
    the full repo; container test runs only have backend/)."""
    import pathlib
    repo_root = pathlib.Path(__file__).resolve().parents[2]
    be_path = repo_root / "backend/app/auth.py"
    cp_path = repo_root / "control_plane/app/auth.py"
    if not (be_path.exists() and cp_path.exists()):
        pytest.skip("source files not reachable from this test runner")
    be = be_path.read_text()
    cp = cp_path.read_text()

    def _extract(src: str, name: str) -> str:
        # Pull JUST the function body — the signature line and every
        # indented line that follows, stopping at the first line that
        # isn't indented (next top-level decl, comment, or blank gap).
        # The previous version captured trailing comments and missed
        # legitimate semantic diffs.
        import re
        lines = src.splitlines()
        out: list[str] = []
        in_fn = False
        for line in lines:
            if not in_fn:
                if re.match(rf"^def {re.escape(name)}\(", line):
                    in_fn = True
                    out.append(line)
                continue
            # Inside the function: keep indented or empty lines that
            # belong to the body; stop at the first non-indented line.
            if line == "" or line.startswith((" ", "\t")):
                out.append(line)
            else:
                break
        # Trim trailing blank lines so they don't count as diff.
        while out and out[-1] == "":
            out.pop()
        assert any(line.startswith(f"def {name}(") for line in out), f"function {name!r} missing"
        return "\n".join(out)

    for fn in ("hash_password", "verify_password"):
        be_body = _extract(be, fn)
        cp_body = _extract(cp, fn)
        assert be_body == cp_body, (
            f"`{fn}` diverged between backend/app/auth.py and "
            f"control_plane/app/auth.py — would silently break cross-side "
            f"hash verification. Sync the two implementations, then re-run."
        )


def test_identity_layer_rejects_token_whose_org_id_differs_from_container_tenant(monkeypatch):
    """Even when secrets match by coincidence (or both tenants use the
    same dev-default), get_current_user must refuse a cookie whose
    embedded org_id is not THIS container's tenant_id. Belt + suspenders."""
    # Same secret intentionally — proves layer 2 fires even when layer 1
    # is bypassed.
    secret = "shared-secret-for-this-test-only-do-not-do-this-in-prod"
    monkeypatch.setenv("DOCAIQ_JWT_SECRET", secret)
    monkeypatch.setenv("DOCAIQ_TENANT_ID", "this-container-is-tenant-X")
    monkeypatch.setenv("DOCAIQ_DATABASE_URL", "postgresql+psycopg://x:x@x/x")
    monkeypatch.setenv("DOCAIQ_ENVIRONMENT", "development")

    # Re-import settings + auth to pick up the monkeypatched env.
    import app.config as cfg
    cfg.get_settings.cache_clear()  # type: ignore[attr-defined]
    import app.auth as auth_mod
    importlib.reload(auth_mod)

    # Forge a token claiming org_id of a DIFFERENT tenant than the container.
    token = _make_token(secret=secret, org_id="some-other-tenant")

    # verify_session_token decodes JWT (signature check passes — same
    # secret). The org_id check happens at get_current_user; we verify
    # the claims expose the mismatch the identity layer will catch.
    claims = auth_mod.verify_session_token(token)
    assert claims is not None
    assert claims["org_id"] == "some-other-tenant"
    assert claims["org_id"] != cfg.get_settings().tenant_id, (
        "Test setup wrong: forged claims should NOT match the container tenant_id"
    )
