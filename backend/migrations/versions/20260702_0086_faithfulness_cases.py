"""faithfulness_cases — chat-faithfulness eval corpus from consented free docs

One row per AI chat answer to a CONSENTED free-tier user: the question, the answer, the
cited evidence, whether it abstained, and (attached later) the human 👍/👎 label from
ChatFeedback. Lets us measure/regression-test RAG faithfulness over real usage. Consent-
gated; paid chats are never captured. Superadmin-export only.

Revision ID: 0086_faithfulness_cases
Revises: 0085_golden_eval_cases
Create Date: 2026-07-02
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0086_faithfulness_cases"
down_revision: Union[str, Sequence[str], None] = "0085_golden_eval_cases"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "faithfulness_cases",
        sa.Column("pk", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=False, index=True),
        sa.Column("message_pk", sa.Integer(), nullable=False),
        sa.Column("doc_id_external", sa.String(length=64), nullable=True),
        sa.Column("scope", sa.String(length=16), nullable=False, server_default="doc"),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=False),
        sa.Column("meta", sa.String(length=64), nullable=True),
        sa.Column("abstained", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("citations", JSONB(), nullable=False, server_default="[]"),
        sa.Column("label", sa.String(length=8), nullable=True),        # up | down
        sa.Column("category", sa.String(length=16), nullable=True),
        sa.Column("suggestion", sa.Text(), nullable=True),
        sa.Column("rating", sa.Integer(), nullable=True),
        sa.Column("verified", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("source", sa.String(length=32), nullable=False, server_default="free_consented"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("tenant_id", "message_pk", name="uq_faithfulness_tenant_msg"),
    )


def downgrade() -> None:
    op.drop_table("faithfulness_cases")
