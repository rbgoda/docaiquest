"""Per-tenant custom category repository (M28.5).

Reads merge canonical (hardcoded) + global (tenant-wide custom) + vendor-local
(custom for one vendor) categories. The canonical list lives in
`app/agents/categorizer.py` — single source of truth — and is imported here
so this module doesn't drift from it.

Writes go through `create()` which enforces the scope/vendor_pk consistency
the DB CHECK constraint also enforces (defense in depth).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.categorizer import EXPENSE_CATEGORIES, INCOME_CATEGORIES
from app.db import get_current_tenant
from app.orm import CustomCategory


def _max_rows() -> int:
    from app.config import get_settings
    return get_settings().max_list_rows


def _to_dict(row: CustomCategory) -> dict:
    return {
        "pk": row.pk,
        "name": row.name,
        "mode": row.mode,
        "scope": row.scope,
        "vendorPk": row.vendor_pk,
        "createdBy": row.created_by,
        "createdAt": row.created_at.isoformat() if row.created_at else None,
    }


def _canonical_dicts(mode: str) -> list[dict]:
    """Synthesize Category dicts for the hardcoded vocab. `pk` is None so
    the frontend renders these without an X (can't delete) and clients
    can tell custom from built-in at a glance."""
    src = EXPENSE_CATEGORIES if mode == "expense" else INCOME_CATEGORIES
    return [
        {"pk": None, "name": n, "mode": mode, "scope": "canonical",
         "vendorPk": None, "createdBy": None, "createdAt": None}
        for n in src
    ]


def list_for_picker(db: Session, *, mode: str, vendor_pk: int | None) -> list[dict]:
    """Return the merged list the frontend dropdown displays. Canonical
    first, then global custom, then vendor-local (when vendor_pk given).
    De-duplicated by (mode, name) — vendor-local wins over global wins
    over canonical when names collide. Empty `mode` (None) returns
    everything; we only support 'expense' / 'income' for now."""
    tid = get_current_tenant()
    out: dict[tuple[str, str], dict] = {}
    # Canonical baseline.
    for c in _canonical_dicts(mode):
        out[(mode, c["name"])] = c
    # Global custom overrides canonical (same name → metadata says custom).
    rows = db.scalars(
        select(CustomCategory).where(
            CustomCategory.tenant_id == tid,
            CustomCategory.mode == mode,
            CustomCategory.scope == "global",
        ).order_by(CustomCategory.name)
    ).all()
    for r in rows:
        out[(mode, r.name)] = _to_dict(r)
    # Vendor-local overrides global. Only included when caller passes a vendor_pk.
    if vendor_pk is not None:
        vrows = db.scalars(
            select(CustomCategory).where(
                CustomCategory.tenant_id == tid,
                CustomCategory.mode == mode,
                CustomCategory.scope == "vendor",
                CustomCategory.vendor_pk == vendor_pk,
            ).order_by(CustomCategory.name)
        ).all()
        for r in vrows:
            out[(mode, r.name)] = _to_dict(r)
    return list(out.values())


def list_custom_names(db: Session, *, mode: str, vendor_pk: int | None) -> list[str]:
    """Just the strings — used by the categorizer to extend its enum.
    Returns canonical + global + vendor-local merged, dedup'd."""
    return [c["name"] for c in list_for_picker(db, mode=mode, vendor_pk=vendor_pk)]


def list_all_custom(db: Session) -> list[dict]:
    """Admin · every custom row for the tenant. Both modes, both scopes,
    all vendors. Ordered by mode → scope → vendor_pk → name so the UI can
    group them stably. Excludes canonical (which doesn't live in this table)."""
    from app.config import get_settings
    tid = get_current_tenant()
    rows = db.scalars(
        select(CustomCategory)
        .where(CustomCategory.tenant_id == tid)
        .order_by(
            CustomCategory.mode,
            CustomCategory.scope.desc(),  # global before vendor (g < v alphabetically; desc reverses)
            CustomCategory.vendor_pk,
            CustomCategory.name,
        )
        .limit(get_settings().max_list_rows)
    ).all()
    return [_to_dict(r) for r in rows]


def create(db: Session, *, name: str, mode: str, scope: str,
           vendor_pk: int | None, created_by: str) -> dict:
    """Insert a new custom category. Returns the dict shape."""
    name = name.strip()
    if not name:
        raise ValueError("name cannot be empty")
    if mode not in ("expense", "income"):
        raise ValueError(f"mode must be 'expense' or 'income', got {mode!r}")
    if scope == "vendor":
        if vendor_pk is None:
            raise ValueError("vendor_pk required when scope='vendor'")
    elif scope == "global":
        vendor_pk = None  # ensure NULL even if caller sent one
    else:
        raise ValueError(f"scope must be 'global' or 'vendor', got {scope!r}")

    tid = get_current_tenant()
    # Skip insert if the name already exists at this scope — return the
    # existing row instead of erroring. Idempotent from the caller's view.
    existing = db.scalar(
        select(CustomCategory).where(
            CustomCategory.tenant_id == tid,
            CustomCategory.mode == mode,
            CustomCategory.scope == scope,
            CustomCategory.vendor_pk == vendor_pk,
            CustomCategory.name == name,
        )
    )
    if existing is not None:
        return _to_dict(existing)

    row = CustomCategory(
        tenant_id=tid, name=name, mode=mode, scope=scope,
        vendor_pk=vendor_pk, created_by=created_by,
    )
    db.add(row)
    db.flush()  # repo-layer commit removed (TODO #12)
    db.refresh(row)
    return _to_dict(row)


def delete(db: Session, pk: int) -> bool:
    """Remove a custom row. Canonical entries (pk=None) can't be deleted —
    they aren't in this table. Returns True when a row was actually deleted."""
    tid = get_current_tenant()
    row = db.scalar(
        select(CustomCategory).where(
            CustomCategory.tenant_id == tid,
            CustomCategory.pk == pk,
        )
    )
    if row is None:
        return False
    db.delete(row)
    db.flush()  # repo-layer commit removed (TODO #12)
    return True
