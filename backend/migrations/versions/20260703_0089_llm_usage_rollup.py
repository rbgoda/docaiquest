"""llm_usage_rollup — bounded pre-aggregated LLM utilization (day 30d + monthly all-time)

The admin 'Model utilization' panel reads this small table instead of scanning llm_call_audit
(~1.5M rows/mo at 1000 users). period='day' rows cover the last 30 days; period='month' rows are
one-per-month and persist after the raw ledger is purged. user_email='' = tenant-wide aggregate.

Revision ID: 0089_llm_usage_rollup
Revises: 0088_promo_codes
Create Date: 2026-07-03
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0089_llm_usage_rollup"
down_revision: Union[str, Sequence[str], None] = "0088_promo_codes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "llm_usage_rollup",
        sa.Column("pk", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("user_email", sa.String(length=256), nullable=False, server_default=""),
        sa.Column("period", sa.String(length=8), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("calls", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("input_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("tenant_id", "user_email", "period", "period_start", "provider", "model",
                            name="uq_llm_rollup"),
    )
    op.create_index("ix_llm_rollup_read", "llm_usage_rollup",
                    ["tenant_id", "user_email", "period", "period_start"])


def downgrade() -> None:
    op.drop_index("ix_llm_rollup_read", table_name="llm_usage_rollup")
    op.drop_table("llm_usage_rollup")
