"""Self-serve API keys for enterprise users.

A logged-in user creates keys scoped to THEIR OWN documents (owner_user_id = the user), then uses them
from their own app against `/api/v1/ask`, `/api/v1/documents`, and `/api/extraction/extract`. The raw
key (`dq_live_…`) is returned exactly once on create and never stored — only its SHA-256 hash + prefix.
Session-authed (the enterprise user's own login), never an API key managing API keys.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import api_keys
from app.db import get_session
from app.orm import ApiClient
from app.security import CurrentUser, get_current_user

router = APIRouter()

# What an enterprise self-serve key may do — read-only over the owner's own data + stateless extract.
_SELF_SERVE_SCOPES = ["ask", "documents:read", "extract"]
_MAX_ACTIVE = 10


class CreateKeyPayload(BaseModel):
    name: str | None = None


def _key_view(c: ApiClient) -> dict:
    return {"id": c.pk, "name": c.name, "keyPrefix": c.key_prefix, "scopes": c.scopes or [],
            "env": c.env, "rpm": c.rate_limit_rpm,
            "createdAt": c.created_at.isoformat() if c.created_at else None,
            "lastUsedAt": c.last_used_at.isoformat() if c.last_used_at else None,
            "revoked": c.revoked_at is not None}


@router.get("/keys")
def list_keys(user: CurrentUser = Depends(get_current_user),
              db: Session = Depends(get_session)) -> dict:
    """List the caller's own API keys (never the raw secret — that's shown once at creation)."""
    rows = db.scalars(select(ApiClient).where(ApiClient.owner_user_id == user.id)
                      .order_by(ApiClient.pk.desc())).all()
    return {"keys": [_key_view(c) for c in rows], "scopes": _SELF_SERVE_SCOPES}


@router.post("/keys")
def create_key(payload: CreateKeyPayload,
               user: CurrentUser = Depends(get_current_user),
               db: Session = Depends(get_session)) -> dict:
    """Mint a new owner-scoped key. Returns the raw key ONCE — copy it now."""
    active = db.scalar(select(func.count()).select_from(ApiClient).where(
        ApiClient.owner_user_id == user.id, ApiClient.revoked_at.is_(None))) or 0
    if active >= _MAX_ACTIVE:
        raise HTTPException(status_code=409,
                            detail=f"you have {active} active keys (max {_MAX_ACTIVE}) — revoke one first")
    raw = api_keys.generate_key("live")
    c = ApiClient(tenant_id=user.org_id, owner_user_id=user.id,
                  name=(payload.name or "").strip()[:128] or "API key",
                  key_prefix=api_keys.key_prefix(raw), key_hash=api_keys.hash_key(raw), env="live",
                  scopes=list(_SELF_SERVE_SCOPES), rate_limit_rpm=60, created_by=user.email)
    db.add(c)
    db.commit()
    db.refresh(c)
    return {"key": raw, "keyInfo": _key_view(c),
            "warning": "Copy this key now — for security it will never be shown again."}


@router.delete("/keys/{key_id}")
def revoke_key(key_id: int = Path(...),
               user: CurrentUser = Depends(get_current_user),
               db: Session = Depends(get_session)) -> dict:
    """Revoke one of the caller's own keys (immediate — the key stops working on the next request)."""
    c = db.get(ApiClient, key_id)
    if c is None or c.owner_user_id != user.id:
        raise HTTPException(status_code=404, detail="key not found")
    if c.revoked_at is None:
        c.revoked_at = datetime.now(timezone.utc)
        db.commit()
    return {"ok": True, "id": key_id}
