from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_current_tenant, get_current_vendor_pk
from app.orm import User, UserRole


def _to_dict(row: User) -> dict:
    return {
        "id": row.pk,
        "email": row.email,
        "name": row.name,
        "roles": sorted(r.role for r in row.roles),
        "createdAt": row.created_at.isoformat() if row.created_at else None,
        "lastLoginAt": row.last_login_at.isoformat() if row.last_login_at else None,
        "hasPassword": row.password_hash is not None,
    }


def list_all(db: Session) -> list[dict]:
    from app.config import get_settings
    tid = get_current_tenant()
    stmt = select(User).where(User.tenant_id == tid)
    # Vendor-only callers should not enumerate cross-vendor users or internal
    # staff. When current_vendor_pk is set, narrow the result to users bound
    # to the same vendor (which always includes the caller themselves).
    vpk = get_current_vendor_pk()
    if vpk is not None:
        stmt = stmt.where(User.vendor_pk == vpk)
    # `.unique()` is required because User.roles is lazy="joined" — a one-to-many
    # eager load produces duplicate parent rows that the SQLAlchemy result must
    # dedupe explicitly.
    rows = db.scalars(stmt.order_by(User.pk).limit(get_settings().max_list_rows)).unique().all()
    return [_to_dict(r) for r in rows]


def get_by_email(db: Session, email: str) -> User | None:
    tid = get_current_tenant()
    return db.scalar(
        select(User).where(User.tenant_id == tid, User.email == email.lower())
    )


def get_by_pk(db: Session, pk: int) -> User | None:
    tid = get_current_tenant()
    return db.scalar(select(User).where(User.tenant_id == tid, User.pk == pk))


def create(
    db: Session,
    *,
    email: str,
    name: str,
    roles: list[str],
    password_hash: str | None = None,
    vendor_pk: int | None = None,
) -> User:
    tid = get_current_tenant()
    user = User(
        tenant_id=tid,
        email=email.lower(),
        name=name,
        password_hash=password_hash,
        vendor_pk=vendor_pk,
        roles=[UserRole(role=r) for r in sorted(set(roles))],
    )
    db.add(user)
    db.flush()
    return user


def set_roles(db: Session, pk: int, roles: list[str]) -> User | None:
    user = get_by_pk(db, pk)
    if user is None:
        return None
    # Flush AFTER clear and BEFORE append: without this intermediate flush,
    # SQLAlchemy emits the new UserRole INSERTs before the old DELETEs in the
    # same unit-of-work, tripping the (user_pk, role) UniqueConstraint when
    # the new role set overlaps the old (e.g. demoting admin+reviewer → reviewer
    # tries to INSERT (user_pk, reviewer) while the old row still exists).
    user.roles.clear()
    db.flush()
    for r in sorted(set(roles)):
        user.roles.append(UserRole(role=r))
    db.flush()
    return user


def delete(db: Session, pk: int) -> bool:
    user = get_by_pk(db, pk)
    if user is None:
        return False
    db.delete(user)
    db.flush()
    return True


def mark_login(db: Session, user: User) -> None:
    user.last_login_at = datetime.now(timezone.utc)
    db.flush()
