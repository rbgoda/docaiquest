"""document_annotations — user-drawn highlights/boxes (M53)

Per-user annotation layer: a drawn region (normalized 0..1) on a document page,
with the captured text (PyMuPDF clip / region-OCR) + an optional note. Owner-scoped
+ cascades on document/user delete.

Revision ID: 0081_document_annotations
Revises: 0080_documents_sha_unique
Create Date: 2026-06-25
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0081_document_annotations"
down_revision: Union[str, Sequence[str], None] = "0080_documents_sha_unique"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "document_annotations",
        sa.Column("pk", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=False, index=True),
        sa.Column("owner_user_id", sa.Integer(),
                  sa.ForeignKey("users.pk", ondelete="CASCADE"), nullable=True, index=True),
        sa.Column("document_pk", sa.Integer(),
                  sa.ForeignKey("documents.pk", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("page", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("x0", sa.Float(), nullable=False),
        sa.Column("y0", sa.Float(), nullable=False),
        sa.Column("x1", sa.Float(), nullable=False),
        sa.Column("y1", sa.Float(), nullable=False),
        sa.Column("captured_text", sa.Text(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("color", sa.String(length=16), nullable=True),
        sa.Column("source", sa.String(length=16), nullable=False, server_default="user"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
    op.create_index("ix_doc_annotations_doc", "document_annotations",
                    ["tenant_id", "document_pk"])


def downgrade() -> None:
    op.drop_index("ix_doc_annotations_doc", table_name="document_annotations")
    op.drop_table("document_annotations")
