"""superadmin LLM provider config (key/enable/model per provider)

Revision ID: 0078_llm_provider_config
Revises: 0077_plan_config
Create Date: 2026-06-17

New table only; missing rows = use env keys, so safe to apply anytime.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0078_llm_provider_config"
down_revision: Union[str, Sequence[str], None] = "0077_plan_config"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "llm_provider_config",
        sa.Column("tenant_id", sa.String(length=64), primary_key=True),
        sa.Column("provider", sa.String(length=24), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("api_key_enc", sa.Text(), nullable=True),
        sa.Column("default_model", sa.String(length=128), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("llm_provider_config")
