"""qa_result — server-side state for the live QA tracker in the admin console

Revision ID: 0095_qa_result
Revises: 0094_embedding_v2
Create Date: 2026-07-05
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0095_qa_result"
down_revision: Union[str, Sequence[str], None] = "0094_embedding_v2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "qa_result",
        sa.Column("pk", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(64), nullable=False, index=True),
        sa.Column("qid", sa.String(64), nullable=False),
        sa.Column("question_text", sa.Text(), nullable=True),
        sa.Column("doc_type", sa.String(64), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="untested"),
        sa.Column("issue", sa.Text(), nullable=True),
        sa.Column("resolution", sa.Text(), nullable=True),
        sa.Column("is_custom", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("last_answer", sa.Text(), nullable=True),
        sa.Column("updated_by", sa.String(256), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("tenant_id", "qid", name="uq_qa_result_tenant_qid"),
    )


def downgrade() -> None:
    op.drop_table("qa_result")
