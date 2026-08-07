"""Tenant-resolution middleware.

Order of precedence on each request:
1. `org_id` claim on a valid session cookie (the post-login path).
2. `DOCAIQ_TENANT_ID` env (unauthenticated paths + background jobs).

The env fallback matters because some endpoints — `/api/health`,
`/api/auth/login`, `/api/auth/google/*`, OpenAPI — run *before* the user has
a session and still need a tenant in context for the user lookup.

Single-tenant-per-container today, so #1 and #2 always agree. M5+ multi-tenant
SaaS deployments may pin tenant to #1 only and remove the env fallback.
"""

from __future__ import annotations

import logging
import secrets as _secrets
from contextvars import ContextVar

import jwt
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from app.auth import verify_session_token
from app.config import get_settings
from app.db import set_current_tenant, set_current_vendor_pk, set_current_reviewer_email
from app.documents_scope import set_current_owner_user_pk


# ── Request-id (TODO #21) ────────────────────────────────────────────────
# Per-request correlation id, threaded through structured logs and surfaced
# on the response header so callers can quote it back in a bug report.
# Set in RequestIdMiddleware, read via `get_request_id()` from anywhere
# inside the request's task tree (FastAPI route handlers, repos, agents).

current_request_id: ContextVar[str | None] = ContextVar("current_request_id", default=None)


def get_request_id() -> str | None:
    """Returns the current request's correlation id, or None outside a
    request context (e.g. worker job startup)."""
    return current_request_id.get()


class RequestIdFilter(logging.Filter):
    """Inject the per-request id into every log record so structured
    backends can pivot on it. When no id is in context (worker job,
    startup), the field is `"-"` rather than missing."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = get_request_id() or "-"
        return True


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Honor an inbound `X-Request-ID` header (e.g. set by a load balancer
    for end-to-end tracing) when present, otherwise mint a fresh one.
    Always echo it back on the response so the client can quote it."""

    HEADER = "X-Request-ID"

    async def dispatch(self, request: Request, call_next) -> Response:
        rid = request.headers.get(self.HEADER) or _secrets.token_urlsafe(12)
        token = current_request_id.set(rid)
        try:
            response = await call_next(request)
            response.headers[self.HEADER] = rid
            return response
        finally:
            current_request_id.reset(token)


def _is_vendor_only(roles: list[str]) -> bool:
    if "vendor" not in (roles or []):
        return False
    return not any(r in roles for r in ("owner", "admin", "reviewer"))


def _is_reviewer_only(roles: list[str]) -> bool:
    """True iff the user's only role is `reviewer`. Owner/admin satisfy any
    role check (and see everything), so they're never reviewer-scoped. A
    vendor-and-reviewer hybrid wouldn't make sense in practice but if it
    appeared we'd let vendor scoping (stricter) take precedence — checked here
    by requiring `reviewer` and nothing else."""
    if not roles:
        return False
    return set(roles) == {"reviewer"}


class TenantMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        settings = get_settings()
        tenant_id = settings.tenant_id  # env fallback
        vendor_pk: int | None = None
        reviewer_email: str | None = None
        owner_user_pk: int | None = None

        session_cookie = request.cookies.get(settings.session_cookie_name)
        if session_cookie:
            try:
                claims = verify_session_token(session_cookie)
                claimed_org = claims.get("org_id")
                # Defense in depth: a session signed for one tenant cannot be
                # presented to a different tenant's container. We refuse to
                # honor the cookie if it disagrees with this container's tenant.
                # EXCEPTION · shared_mode (M37 free SaaS) · this container
                # legitimately holds many tenants. The JWT signature is the
                # only authentication (one shared JWT secret, no per-tenant
                # secret). Trust whatever org_id the signed JWT claims.
                if claimed_org and (settings.shared_mode or claimed_org == settings.tenant_id):
                    tenant_id = claimed_org
                    roles = claims.get("roles", []) or []
                    # M17 phase 3 · vendor scope from the JWT. Set here in the
                    # middleware (main async task) rather than in the sync
                    # `get_current_user` dependency — sync deps run in a
                    # threadpool and ContextVar mutations there don't
                    # propagate back to the route handler.
                    if _is_vendor_only(roles):
                        vp = claims.get("vendor_pk")
                        if isinstance(vp, int):
                            vendor_pk = vp
                    elif _is_reviewer_only(roles):
                        # Reviewer scoping · the user's email is the scope key
                        # (matches the lead-reviewer / primary-reviewer string
                        # fields on the audit/vendor models). Same
                        # ContextVar-in-middleware reasoning as vendor scope.
                        em = claims.get("email")
                        if isinstance(em, str) and em:
                            reviewer_email = em
                    # M46 · Documents product · per-user workspace isolation.
                    # In a documents stack EVERY user (regardless of role) is
                    # scoped to their own documents/chats. The user's pk lives
                    # in the `sub` claim. Set here in the async middleware for
                    # the same threadpool-ContextVar reason as vendor scope.
                    if settings.product == "documents":
                        try:
                            owner_user_pk = int(claims.get("sub"))
                        except (TypeError, ValueError):
                            # M46 · §4 · FAIL CLOSED. A decoded-but-malformed
                            # documents token (no usable `sub`) must NOT fall
                            # through to an unscoped query — that would expose
                            # every user's docs. Set the deny sentinel (0); the
                            # owner filters match nothing, so the request sees
                            # no rows instead of all of them.
                            owner_user_pk = 0
                # Else: leave tenant_id as the env value. The downstream auth
                # dependency will 401 because the cookie's org_id won't match.
            except jwt.PyJWTError:
                pass  # invalid/expired — anonymous request, env tenant applies

        set_current_tenant(tenant_id)
        set_current_vendor_pk(vendor_pk)
        set_current_reviewer_email(reviewer_email)
        set_current_owner_user_pk(owner_user_pk)
        try:
            response = await call_next(request)
        finally:
            set_current_tenant(None)
            set_current_vendor_pk(None)
            set_current_reviewer_email(None)
            set_current_owner_user_pk(None)
        return response
