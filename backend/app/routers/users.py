"""Tenant-scoped user management. Admin-or-above for all mutations; list is
open to any authenticated user (Settings → Team is visible to everyone, but
the invite/role/delete controls are role-gated client-side and server-side).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session

from app.auth import hash_password
from app.config import get_settings
from app.db import get_session
from app.repositories import users as repo
from app.security import CurrentUser, require_role

router = APIRouter()


# ---- Models -----------------------------------------------------------------
class UserDTO(BaseModel):
    id: int
    email: str
    name: str
    roles: list[str]
    createdAt: str | None = None
    lastLoginAt: str | None = None
    hasPassword: bool


class InvitePayload(BaseModel):
    email: EmailStr
    name: str = Field(min_length=1, max_length=256)
    roles: list[str] = Field(default_factory=list)
    devPassword: str | None = Field(
        default=None,
        description="Optional dev-mode password. Ignored in non-dev environments.",
    )


class RolesPayload(BaseModel):
    roles: list[str]


_VALID_ROLES = {"owner", "admin", "reviewer"}


def _validate_roles(roles: list[str]) -> list[str]:
    invalid = [r for r in roles if r not in _VALID_ROLES]
    if invalid:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid roles: {invalid}. Valid: {sorted(_VALID_ROLES)}",
        )
    return roles


# ---- Routes -----------------------------------------------------------------
@router.get("", response_model=list[UserDTO])
def list_users(db: Session = Depends(get_session)) -> list[dict]:
    return repo.list_all(db)


def _guard_owner_grant(caller: CurrentUser, requested_roles: list[str]) -> None:
    """Only an owner may grant or revoke the `owner` role. Admin/reviewer
    callers attempting it get 403. This guards both invite (grant on create)
    and role-edit (grant via update). The frontend hides the option but the
    server must enforce it — this is the same separation-of-duties story as
    'admin cannot delete the owner' (handled separately in delete_user)."""
    if "owner" in (requested_roles or []) and "owner" not in caller.roles:
        raise HTTPException(
            status_code=403,
            detail="Only an owner can grant the 'owner' role.",
        )


@router.post("", response_model=UserDTO, status_code=201)
def invite_user(
    payload: InvitePayload,
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_role("admin")),
) -> dict:
    _validate_roles(payload.roles)
    _guard_owner_grant(user, payload.roles or [])
    if repo.get_by_email(db, payload.email):
        raise HTTPException(status_code=409, detail=f"User {payload.email} already exists")

    password_hash: str | None = None
    if get_settings().auth_provider == "dev" and payload.devPassword:
        password_hash = hash_password(payload.devPassword)

    user = repo.create(
        db,
        email=str(payload.email),
        name=payload.name,
        roles=payload.roles or ["reviewer"],
        password_hash=password_hash,
    )
    return repo._to_dict(user)  # noqa: SLF001 — internal module use


@router.patch("/{user_id}/roles", response_model=UserDTO)
def update_user_roles(
    user_id: int,
    payload: RolesPayload,
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_role("admin")),
) -> dict:
    _validate_roles(payload.roles)
    # Block admins from granting OR revoking owner. Revoke is just as
    # dangerous — an admin could otherwise nuke the owner's powers and then
    # promote themselves. We check both the requested role set AND the
    # target user's existing roles.
    target = repo.get_by_pk(db, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail=f"User {user_id} not found")
    if "owner" not in user.roles:
        if "owner" in (payload.roles or []) or "owner" in [r.role for r in target.roles]:
            raise HTTPException(
                status_code=403,
                detail="Only an owner can grant or revoke the 'owner' role.",
            )
    updated = repo.set_roles(db, user_id, payload.roles)
    if updated is None:
        raise HTTPException(status_code=404, detail=f"User {user_id} not found")
    return repo._to_dict(updated)  # noqa: SLF001


@router.delete("/{user_id}", status_code=204)
def delete_user(
    user_id: int,
    db: Session = Depends(get_session),
    current: CurrentUser = Depends(require_role("admin")),
) -> None:
    if current.id == user_id:
        raise HTTPException(status_code=400, detail="Cannot delete your own account")
    if not repo.delete(db, user_id):
        raise HTTPException(status_code=404, detail=f"User {user_id} not found")
