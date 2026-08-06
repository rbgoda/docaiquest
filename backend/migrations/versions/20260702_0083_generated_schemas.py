"""generated_schemas — crystallized per-type extraction schemas (Move-1 PR3)

The nightly schema_crystallize job distils a stable LearnedSchema cluster into a
concrete typed schema; when active the universal extractor promotes its labels to
first-class fields. Field-names only (never document values). Tenant-scoped.

Revision ID: 0083_generated_schemas
Revises: 0082_feedback_resolution
Create Date: 2026-07-02
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0083_generated_schemas"
down_revision: Union[str, Sequence[str], None] = "0082_feedback_resolution"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "generated_schemas",
        sa.Column("pk", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=False, index=True),
        sa.Column("cluster_key", sa.String(length=128), nullable=False),
        sa.Column("label", sa.String(length=128), nullable=True),
        sa.Column("fields", JSONB(), nullable=False, server_default="{}"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="proposed"),
        sa.Column("source", sa.String(length=32), nullable=False, server_default="crystallize"),
        sa.Column("seen_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.UniqueConstraint("tenant_id", "cluster_key", name="uq_generated_schemas_tenant_cluster"),
    )


def downgrade() -> None:
    op.drop_table("generated_schemas")
