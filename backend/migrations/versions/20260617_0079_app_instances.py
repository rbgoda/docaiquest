"""fleet registry of app instances (enterprise dedicated containers)

Revision ID: 0079_app_instances
Revises: 0078_llm_provider_config
Create Date: 2026-06-17
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0079_app_instances"
down_revision: Union[str, Sequence[str], None] = "0078_llm_provider_config"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "app_instances",
        sa.Column("instance_id", sa.String(length=64), primary_key=True),
        sa.Column("name", sa.String(length=120), nullable=True),
        sa.Column("hostname", sa.String(length=255), nullable=True),
        sa.Column("ip", sa.String(length=64), nullable=True),
        sa.Column("version", sa.String(length=32), nullable=True),
        sa.Column("plan", sa.String(length=16), nullable=False, server_default="enterprise"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("meta", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("app_instances")
