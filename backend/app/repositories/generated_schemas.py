"""Move-1 PR3b · read access to crystallized schemas for extraction adoption.

The nightly crystallizer (services/schema_crystallizer) writes GeneratedSchema
rows; the universal extractor reads the ACTIVE one for a predicted cluster and
promotes its labels to first-class fields. Tenant-scoped; field-names only.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_current_tenant
from app.orm import GeneratedSchema


def active_fields_for(db: Session, cluster_key: str | None) -> dict | None:
    """The typed-field map of the ACTIVE crystallized schema for this cluster, or
    None when there isn't one. `{field_label: {type, description}}`."""
    if not cluster_key:
        return None
    row = db.scalar(select(GeneratedSchema).where(
        GeneratedSchema.tenant_id == get_current_tenant(),
        GeneratedSchema.cluster_key == cluster_key,
        GeneratedSchema.status == "active",
    ))
    if row is None or not row.fields:
        return None
    return dict(row.fields)
