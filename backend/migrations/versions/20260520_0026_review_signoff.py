"""Reviewer sign-off · documents.review_* columns + document_reviews trail

Adds three columns to `documents` for the live review state:
  review_status   'pending' | 'reviewed' | 'exception'   default 'pending'
  reviewed_by     reviewer email                          nullable
  reviewed_at     timestamp                               nullable
  review_note     text — context the reviewer captured    nullable

And a `document_reviews` table for the audit trail (every status change
across the doc's lifetime). The columns on documents are the CURRENT
state; the table is the HISTORY (who flipped status from X to Y, when,
with what reason). Same pattern as field_edits.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0026_review_signoff"
down_revision: Union[str, Sequence[str], None] = "0025_field_edits"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Live review state on documents
    op.add_column("documents", sa.Column(
        "review_status", sa.String(32),
        nullable=False, server_default="pending",
    ))
    op.add_column("documents", sa.Column("review_note", sa.Text, nullable=True))
    op.add_column("documents", sa.Column("reviewed_by", sa.String(256), nullable=True))
    op.add_column("documents", sa.Column(
        "reviewed_at", sa.DateTime(timezone=True), nullable=True,
    ))
    op.create_index(
        "ix_documents_review_status",
        "documents",
        ["tenant_id", "review_status"],
    )

    # Audit trail of every status flip
    op.create_table(
        "document_reviews",
        sa.Column("pk", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(64), nullable=False, index=True),
        sa.Column(
            "document_pk",
            sa.Integer,
            sa.ForeignKey("documents.pk", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("prior_status", sa.String(32), nullable=True),
        sa.Column("new_status", sa.String(32), nullable=False),
        sa.Column("reviewed_by", sa.String(256), nullable=False),
        sa.Column("reason", sa.Text, nullable=True),
        sa.Column(
            "reviewed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )


def downgrade() -> None:
    op.drop_table("document_reviews")
    op.drop_index("ix_documents_review_status", table_name="documents")
    op.drop_column("documents", "reviewed_at")
    op.drop_column("documents", "reviewed_by")
    op.drop_column("documents", "review_note")
    op.drop_column("documents", "review_status")
