"""superadmin-configurable plan limits/toggles

Revision ID: 0077_plan_config
Revises: 0076_feedback_screenshots
Create Date: 2026-06-17

New table only. Missing rows fall back to code defaults, so this is safe to
apply before any row exists.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0077_plan_config"
down_revision: Union[str, Sequence[str], None] = "0076_feedback_screenshots"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "plan_config",
        sa.Column("tenant_id", sa.String(length=64), primary_key=True),
        sa.Column("plan", sa.String(length=16), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("docs_monthly", sa.Integer(), nullable=True),
        sa.Column("ai_monthly", sa.Integer(), nullable=True),
        sa.Column("features", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("paid_models", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("llm_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("dedicated_container", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("plan_config")
