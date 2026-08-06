"""M46 · learned_doc_types — self-learning classification registry.

Per-user open-vocabulary doc types the reconciler derives from a doc's AI
summary when the closed-enum classifier returns 'other'. Additive.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0054_learned_doc_types"
down_revision: Union[str, Sequence[str], None] = "0053_chat_feedback_screenshots"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "learned_doc_types",
        sa.Column("pk", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("owner_user_id", sa.Integer(), nullable=True),
        sa.Column("type_slug", sa.String(length=64), nullable=False),
        sa.Column("label", sa.String(length=128), nullable=True),
        sa.Column("source", sa.String(length=16), server_default="ai", nullable=False),
        sa.Column("seen_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("tenant_id", "owner_user_id", "type_slug", name="uq_learned_doc_types"),
    )
    op.create_index("ix_learned_doc_types_tenant_id", "learned_doc_types", ["tenant_id"])
    op.create_index("ix_learned_doc_types_owner_user_id", "learned_doc_types", ["owner_user_id"])


def downgrade() -> None:
    op.drop_index("ix_learned_doc_types_owner_user_id", table_name="learned_doc_types")
    op.drop_index("ix_learned_doc_types_tenant_id", table_name="learned_doc_types")
    op.drop_table("learned_doc_types")
