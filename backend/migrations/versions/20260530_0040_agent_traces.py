"""M44.P2 · agent_traces · ReAct-loop step persistence.

One row per agent step (thought + action + observation). The FK on
chat_message_pk lets the frontend hydrate the full trace lazily when
the reviewer clicks "Show reasoning" on an agent-produced answer.

Step ordering is by `step_index` (0-based, monotonic per chat message).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0040_agent_traces"
down_revision: Union[str, Sequence[str], None] = "0039_reflexion_pairs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_traces",
        sa.Column("pk", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=False, index=True),
        sa.Column(
            "chat_message_pk", sa.Integer(),
            sa.ForeignKey("chat_messages.pk", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("step_index", sa.Integer(), nullable=False),
        sa.Column("thought", sa.Text(), nullable=True),
        sa.Column("action_name", sa.String(length=64), nullable=True),
        sa.Column("action_args", JSONB(), nullable=True),
        sa.Column("observation", sa.Text(), nullable=True),
        sa.Column("observation_meta", JSONB(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
    )
    op.create_index(
        "ix_agent_traces_msg",
        "agent_traces",
        ["tenant_id", "chat_message_pk", "step_index"],
    )


def downgrade() -> None:
    op.drop_index("ix_agent_traces_msg", table_name="agent_traces")
    op.drop_table("agent_traces")
