"""SQLAlchemy plumbing + tenant context.

The tenant is the single most important read filter in the platform. Every
repository query reads it from the contextvar `current_tenant`; the request
middleware in `app/middleware.py` sets that contextvar per request from the
JWT (M5) or from `DOCAIQ_TENANT_ID` (today). Background jobs that need to
operate on a tenant call `set_current_tenant(...)` directly.

Sync engine + sync sessions. FastAPI runs sync route handlers in a threadpool,
which is enough for our load — async DB is a future optimization, not an M4
requirement.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    """Declarative base. Every ORM model inherits from this; Alembic picks up
    everything attached to Base.metadata."""


_settings = get_settings()

engine = create_engine(
    _settings.database_url,
    # Pool sized for a single-tenant container. Bump in M11 if we run many
    # tenants on a shared backend (we won't — one container per tenant).
    pool_size=5,
    max_overflow=10,
    pool_pre_ping=True,
    future=True,
)

SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


# ---- tenant context -----------------------------------------------------
# Set by the middleware on every request; read by every repository query.
# `None` is only valid during app boot before any request — repos that need
# a tenant raise `MissingTenantError` rather than silently leaking data.
current_tenant: ContextVar[str | None] = ContextVar("current_tenant", default=None)

# ---- vendor scope (M17 phase 3) -----------------------------------------
# Set after `get_current_user` runs for vendor-only users. Repository queries
# that touch docs / RFIs / audit_runs / requirements read this to add a
# secondary filter on top of the tenant scope. None = no additional filter
# (user is admin/reviewer/owner and sees all vendors in the tenant).
current_vendor_pk: ContextVar[int | None] = ContextVar("current_vendor_pk", default=None)

# ---- reviewer scope -----------------------------------------------------
# Set in TenantMiddleware for users whose only role is `reviewer`. Vendors +
# audit_runs repositories use this to restrict the list to assignments
# (vendor.primary_reviewer == email OR audit_runs.lead_reviewer == email).
# None = no additional filter — admin/owner see everything; vendor users go
# through the stricter vendor_pk path. Email is stored case-as-given since
# we compare against unnormalised columns that may hold mixed case.
current_reviewer_email: ContextVar[str | None] = ContextVar("current_reviewer_email", default=None)


class MissingTenantError(RuntimeError):
    """Raised when a repository query runs without a tenant in context.
    Almost always a missing middleware or a misconfigured background job."""


def get_current_tenant() -> str:
    tid = current_tenant.get()
    if not tid:
        raise MissingTenantError(
            "No tenant in context. Did the request hit the tenant middleware?"
        )
    return tid


def set_current_tenant(tenant_id: str | None) -> None:
    """Set the tenant for the current async/threadlocal context."""
    current_tenant.set(tenant_id)


def get_current_vendor_pk() -> int | None:
    """Returns the vendor_pk filter for the current request, or None if the
    user isn't vendor-scoped. Never raises — None is a valid state."""
    return current_vendor_pk.get()


def set_current_vendor_pk(pk: int | None) -> None:
    current_vendor_pk.set(pk)


def get_current_reviewer_email() -> str | None:
    """Returns the reviewer-scope email for the current request, or None if
    the user isn't reviewer-scoped. Never raises — None is a valid state."""
    return current_reviewer_email.get()


def set_current_reviewer_email(email: str | None) -> None:
    current_reviewer_email.set(email)


# ---- FastAPI dependency -------------------------------------------------
def get_session() -> Iterator[Session]:
    """Yield a session, commit on success, rollback on exception, close always.

    TRANSACTIONAL CONTRACT (TODO #12 + #35):
    ----------------------------------------
    * REPOSITORIES MUST NOT call `db.commit()`. They `db.flush()` so
      writes are visible to subsequent queries in the same unit of work.
      Repo-level commits caused partial-success bugs across multi-repo
      handlers (vendor row committed, audit_run row failed → orphaned data).
      Enforcement: zero `db.commit` in `app/repositories/` (grep gate).
    * ROUTERS MUST NOT call `db.commit()` UNLESS the commit is required
      mid-handler — e.g. to make a row visible to a BackgroundTask /
      enqueued Arq job. Every such site MUST have a comment justifying it.
      The default success path is "let get_session commit at request end."
      Where this gets messy is documents.py / doc_chat.py which were
      refactored to use mid-handler commits before the rule existed; #35
      cleans those up case-by-case, not mechanically.
    * AGENTS / workers / scripts open their own SessionLocal and commit
      explicitly per unit of work.
    """
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
