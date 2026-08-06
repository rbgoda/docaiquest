"""document_text_overrides — human-corrected full-text / Markdown per document

One row per document. The deterministic Markdown (built from the parsed chunks) is the
default; when a reviewer edits it, the corrected text lives here and is served in
preference. Chunks + embeddings are untouched, so retrieval / RAG are unaffected.

Revision ID: 0087_document_text_overrides
Revises: 0086_faithfulness_cases
Create Date: 2026-07-03
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0087_document_text_overrides"
down_revision: Union[str, Sequence[str], None] = "0086_faithfulness_cases"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "document_text_overrides",
        sa.Column("pk", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=False, index=True),
        sa.Column("document_pk", sa.Integer(), nullable=False),
        sa.Column("markdown", sa.Text(), nullable=False),
        sa.Column("edited_by", sa.String(length=256), nullable=False),
        sa.Column("edited_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["document_pk"], ["documents.pk"], ondelete="CASCADE"),
        sa.UniqueConstraint("document_pk", name="uq_text_override_document"),
    )
    # NB: the tenant_id column above is declared index=True, which already creates
    # ix_document_text_overrides_tenant_id — no explicit create_index (that duplicates it).


def downgrade() -> None:
    op.drop_table("document_text_overrides")
