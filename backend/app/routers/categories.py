"""Per-tenant category vocabulary endpoints (M28.5).

Reads are open to any authenticated user — the picker UI calls this on
every doc-panel open. Writes are gated:
  - scope='vendor' → reviewer/admin/owner (reviewers must be assigned to
    the vendor; the existing reviewer_clause in vendors_repo enforces it
    transitively when we look up the vendor for the create).
  - scope='global' → admin/owner only.

Deletes: admin/owner only. Reviewers can request deletion via chat / RFI.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db import get_session
from app.models.categories import Category, CategoryCreate
from app.repositories import categories as repo
from app.security import CurrentUser, get_current_user, require_role

router = APIRouter()


@router.get("", response_model=list[Category])
def list_categories(
    mode: str = Query(..., description="expense | income"),
    vendor_pk: int | None = Query(None, description="when set, includes vendor-local entries"),
    db: Session = Depends(get_session),
    _user: CurrentUser = Depends(get_current_user),
) -> list[dict]:
    if mode not in ("expense", "income"):
        raise HTTPException(status_code=400, detail="mode must be 'expense' or 'income'")
    return repo.list_for_picker(db, mode=mode, vendor_pk=vendor_pk)


@router.get("/admin/all", response_model=list[Category])
def list_all_custom_categories(
    db: Session = Depends(get_session),
    _user: CurrentUser = Depends(require_role("admin")),
) -> list[dict]:
    """Admin-only · every CUSTOM category for the tenant (both modes, both
    scopes). Used by the Settings → Categories management screen. Excludes
    canonical entries — those can't be deleted and don't need management."""
    return repo.list_all_custom(db)


@router.post("", response_model=Category)
def create_category(
    payload: CategoryCreate,
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_role("admin", "reviewer")),
) -> dict:
    # Reviewers can only create vendor-local entries. Admin / owner can
    # create either scope. Owner is implicit admin (require_role handles it).
    if payload.scope == "global" and not (user.has_role("admin") or user.has_role("owner")):
        raise HTTPException(
            status_code=403,
            detail="Only admins or owners can add global categories. "
                   "Reviewers can add vendor-local categories.",
        )
    try:
        return repo.create(
            db,
            name=payload.name,
            mode=payload.mode,
            scope=payload.scope,
            vendor_pk=payload.vendorPk,
            created_by=user.email,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/{pk}", status_code=204)
def delete_category(
    pk: int,
    db: Session = Depends(get_session),
    _user: CurrentUser = Depends(require_role("admin")),
) -> None:
    if not repo.delete(db, pk):
        raise HTTPException(status_code=404, detail=f"Category {pk} not found")
