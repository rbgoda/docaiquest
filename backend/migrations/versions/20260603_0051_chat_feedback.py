"""M46 · chat_feedback · thumbs-up/down + free-text feedback on documents chat
answers, feeding the improvement loop. Additive; documents product writes it.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0051_chat_feedback"
down_revision: Union[str, Sequence[str], None] = "0050_learned_schemas"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chat_feedback",
        sa.Column("pk", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("owner_user_id", sa.Integer(), nullable=True),
        sa.Column("message_pk", sa.Integer(), nullable=False),
        sa.Column("direction", sa.String(length=8), nullable=False),
        sa.Column("feedback", sa.Text(), nullable=True),
        sa.Column("question", sa.Text(), nullable=True),
        sa.Column("answer_excerpt", sa.Text(), nullable=True),
        sa.Column("doc_id", sa.String(length=128), nullable=True),
        sa.Column("resolved", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_chat_feedback_tenant_id", "chat_feedback", ["tenant_id"])
    op.create_index("ix_chat_feedback_owner_user_id", "chat_feedback", ["owner_user_id"])


def downgrade() -> None:
    op.drop_index("ix_chat_feedback_owner_user_id", table_name="chat_feedback")
    op.drop_index("ix_chat_feedback_tenant_id", table_name="chat_feedback")
    op.drop_table("chat_feedback")
