from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_current_tenant
from app.orm import RoutingConfig


def get(db: Session) -> dict | None:
    """Returns the tenant's routing config blob, or None if not yet set."""
    tid = get_current_tenant()
    row = db.scalar(select(RoutingConfig).where(RoutingConfig.tenant_id == tid))
    return row.config if row else None


def upsert(db: Session, config: dict) -> dict:
    tid = get_current_tenant()
    row = db.scalar(select(RoutingConfig).where(RoutingConfig.tenant_id == tid))
    if row is None:
        row = RoutingConfig(tenant_id=tid, config=config)
        db.add(row)
    else:
        row.config = config
    db.flush()
    return row.config
