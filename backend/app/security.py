"""FastAPI dependencies for auth. Routes that need a logged-in user use
`Depends(current_user)`; routes that need a role use `Depends(require_role(...))`.

The tenant scope is enforced separately by the repository layer via the
`current_tenant` contextvar in `app.db`; this module's job is *identity*.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import jwt
from fastapi import Cookie, Depends, HTTPException, status

from app.auth import verify_session_token
from app.config import get_settings
from app.db import set_current_vendor_pk


# Sentinel header for "this cookie was issued by us and presented to the
# correct tenant." The check happens inside `_make_current_user_dependency`.


@dataclass(frozen=True)
class CurrentUser:
    id: int
    email: str
    name: str
    org_id: str
    roles: tuple[str, ...]
    # M17 phase 3 · for users whose only role is `vendor`, this is the
    # primary key of the Vendor row they're bound to. Repositories use
    # `vendor_scope(user)` to derive a filter for the SQL WHERE clause.
    vendor_pk: int | None = None

    def has_role(self, *roles: str) -> bool:
        return any(r in self.roles for r in roles)

    @property
    def is_vendor_only(self) -> bool:
        """True when this user has the vendor role and NOT any of the
        privileged tenant-side roles. Drives repository filtering — a
        person who is both reviewer + vendor (rare) keeps full access."""
        if "vendor" not in self.roles:
            return False
        return not any(r in self.roles for r in ("owner", "admin", "reviewer"))


def vendor_scope(user: CurrentUser) -> int | None:
    """Returns the vendor_pk that repositories should filter by, or None
    if the user is not vendor-only (= no additional filtering needed
    beyond the tenant scope)."""
    return user.vendor_pk if user.is_vendor_only else None


# Role hierarchy: owner > admin > reviewer > vendor. Set membership not
# implication — a `reviewer` does not have `admin` rights, but `owner` is
# typically granted all roles explicitly.
ROLE_HIERARCHY = ["owner", "admin", "reviewer", "vendor"]


# The cookie name is configurable (DOCAIQ_SESSION_COOKIE_NAME). FastAPI's
# Cookie() reads it by the parameter's alias, which we have to bind at import
# time — hence the factory pattern.
def _make_current_user_dependency() -> Callable[..., CurrentUser]:
    cookie_name = get_settings().session_cookie_name

    settings = get_settings()

    def _dep(session_cookie: str | None = Cookie(default=None, alias=cookie_name)) -> CurrentUser:
        if not session_cookie:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Not authenticated",
            )
        try:
            claims = verify_session_token(session_cookie)
        except jwt.PyJWTError as e:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Invalid session: {e}",
            )

        # **Cookie–tenant binding.** A JWT signed for tenant A presented to
        # tenant B's container is rejected, even if the signing secret happens
        # to match. This is depth-in-defense; the primary protection is each
        # tenant having its own DOCAIQ_JWT_SECRET, so foreign cookies never
        # decode in the first place.
        # EXCEPTION · shared_mode · one container holds many tenants, JWT
        # signature is the sole authentication. Skip the binding check.
        if not settings.shared_mode and claims.get("org_id") != settings.tenant_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Session does not belong to this tenant",
            )

        # M46 · §4 · a malformed/forged token with a non-numeric `sub` must 401
        # cleanly (not 500). Combined with the middleware's deny-sentinel, this
        # fails closed at both the auth and data layers.
        try:
            _uid = int(claims["sub"])
        except (TypeError, ValueError, KeyError):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Malformed session token",
            )
        # Session revocation (flag-gated). Only fires when the feature is on AND the
        # token carries a `tv` claim (issued after the feature) — so the flag-off path
        # and any pre-feature session skip the DB entirely (zero impact). One cheap
        # indexed lookup rejects a token whose version is behind the user's current
        # token_version (logout-all / password change) or whose account is frozen.
        if get_settings().session_revocation and "tv" in claims:
            from sqlalchemy import select as _select

            from app.db import SessionLocal
            from app.orm import User as _User
            with SessionLocal() as _s:
                row = _s.execute(_select(_User.token_version, _User.is_frozen)
                                 .where(_User.pk == _uid)).first()
            if row is not None:
                if row.is_frozen:
                    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                                        detail="Your workspace is under review — please sign in again.")
                if int(claims.get("tv", 0) or 0) < int(row.token_version or 0):
                    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                                        detail="Session ended — please sign in again.")
        user = CurrentUser(
            id=_uid,
            email=claims["email"],
            name=claims["name"],
            org_id=claims["org_id"],
            roles=tuple(claims.get("roles", []) or []),
            vendor_pk=claims.get("vendor_pk"),
        )
        # Push vendor scope into the per-request ContextVar so any repository
        # query in this request automatically respects it. Non-vendor users
        # explicitly set None to clear any leftover scope from a previous
        # request running on the same worker.
        set_current_vendor_pk(vendor_scope(user))
        return user

    return _dep


# Public dependency — import this from routers.
get_current_user = _make_current_user_dependency()


def require_role(*allowed_roles: str) -> Callable[..., CurrentUser]:
    """Returns a dependency that 403s if the user has none of the required roles.
    `owner` implicitly satisfies any check — owners can do everything in their tenant."""

    def _dep(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if "owner" in user.roles or user.has_role(*allowed_roles):
            return user
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Requires one of: {', '.join(allowed_roles)} (you have: {', '.join(user.roles) or 'none'})",
        )

    return _dep
