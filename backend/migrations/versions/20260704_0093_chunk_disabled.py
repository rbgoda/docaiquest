"""document_chunks: disabled flag for the chunk-inspection tab

A reviewer can exclude a chunk from retrieval (BM25 + vector) without deleting it. Retrieval
skips rows where disabled IS TRUE; the row is kept for provenance and can be re-enabled.

Revision ID: 0093_chunk_disabled
Revises: 0092_rendered_markdown
Create Date: 2026-07-04
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0093_chunk_disabled"
down_revision: Union[str, Sequence[str], None] = "0092_rendered_markdown"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("document_chunks", sa.Column(
        "disabled", sa.Boolean(), nullable=False, server_default=sa.text("false")))


def downgrade() -> None:
    op.drop_column("document_chunks", "disabled")
