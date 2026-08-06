"""M46 · Documents System · per-user isolation primitives.

Kept OUT of the shared `app.db` so the documents per-user scope lives in its own
documents-owned module (minimising the documents footprint on shared files).
The ContextVar is set by TenantMiddleware ONLY when DOCAIQ_PRODUCT=documents;
documents repositories / retrieval / chat read it to scope every query to the
logged-in user's own workspace. None = no per-user filter (always the case in
the auditing product, so audit behaviour is unchanged).
"""
from contextvars import ContextVar

current_owner_user_pk: ContextVar[int | None] = ContextVar("current_owner_user_pk", default=None)


def get_current_owner_user_pk() -> int | None:
    """Returns the per-user owner scope (Documents product), or None when the
    request isn't user-scoped. Never raises — None is a valid state."""
    return current_owner_user_pk.get()


def set_current_owner_user_pk(pk: int | None) -> None:
    current_owner_user_pk.set(pk)
