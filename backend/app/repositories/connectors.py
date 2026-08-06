"""M46 · connector_accounts repository — tenant + per-user scoped.

A user only ever sees/uses their own connector account. Scope = current tenant
+ current owner user (the documents-product per-user scope). Outside the
documents product these aren't used (the connector router is product-gated).
"""
from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db import get_current_tenant
from app.documents_scope import get_current_owner_user_pk
from app.orm import ConnectorAccount


def _owner_pk() -> int:
    pk = get_current_owner_user_pk()
    if pk is None:
        # The router is documents-product gated, where the middleware always
        # sets the owner scope. A None here means a misconfiguration.
        raise RuntimeError("connector accounts require a per-user owner scope")
    return pk


def get(db: Session, provider: str = "drive") -> ConnectorAccount | None:
    return db.scalar(
        select(ConnectorAccount).where(
            ConnectorAccount.tenant_id == get_current_tenant(),
            ConnectorAccount.owner_user_id == _owner_pk(),
            ConnectorAccount.provider == provider,
        )
    )


def upsert(db: Session, *, provider: str, backend: str,
           access_token: str | None, refresh_token: str | None,
           account_email: str | None) -> ConnectorAccount:
    row = get(db, provider)
    if row is None:
        row = ConnectorAccount(
            tenant_id=get_current_tenant(),
            owner_user_id=_owner_pk(),
            provider=provider,
            backend=backend,
        )
        db.add(row)
    row.backend = backend
    # M50 · encrypt OAuth tokens at rest (per-owner key). Keep an existing refresh
    # token if the new exchange didn't return one (Google only returns it on first
    # consent).
    from app import drive_crypto
    _own = row.owner_user_id
    if access_token is not None:
        row.access_token = drive_crypto.encrypt_token(_own, access_token)
    if refresh_token is not None:
        row.refresh_token = drive_crypto.encrypt_token(_own, refresh_token)
    if account_email is not None:
        row.account_email = account_email
    db.flush()
    return row


def disconnect(db: Session, provider: str = "drive") -> bool:
    res = db.execute(
        delete(ConnectorAccount).where(
            ConnectorAccount.tenant_id == get_current_tenant(),
            ConnectorAccount.owner_user_id == _owner_pk(),
            ConnectorAccount.provider == provider,
        )
    )
    return res.rowcount > 0
