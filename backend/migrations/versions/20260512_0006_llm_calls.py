"""llm_calls — per-call cost + trace ledger

Revision ID: 0006_llm_calls
Revises: 0005_retrieval
Create Date: 2026-05-12 20:00:00

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0006_llm_calls"
down_revision: Union[str, Sequence[str], None] = "0005_retrieval"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "llm_calls",
        sa.Column("pk", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("requirement_id_external", sa.String(64), nullable=True),
        sa.Column("chat_message_pk", sa.Integer, sa.ForeignKey("chat_messages.pk", ondelete="SET NULL"), nullable=True),
        sa.Column("task_type", sa.String(32), nullable=False),
        sa.Column("tier", sa.String(8), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("input_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("cost_usd", sa.Float, nullable=False, server_default="0"),
        sa.Column("latency_ms", sa.Integer, nullable=False, server_default="0"),
        sa.Column("confidence", sa.Float, nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("trace", JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_llm_calls_tenant_id", "llm_calls", ["tenant_id"])
    op.create_index("ix_llm_calls_chat_message_pk", "llm_calls", ["chat_message_pk"])


def downgrade() -> None:
    op.drop_index("ix_llm_calls_chat_message_pk", table_name="llm_calls")
    op.drop_index("ix_llm_calls_tenant_id", table_name="llm_calls")
    op.drop_table("llm_calls")
