"""document_chunks: dual-column BGE-M3 embedding_v2 (Vector 1024) + HNSW index

Retrieval Step 2 (Phase 2a). Adds a SECOND embedding column alongside the live 384-dim MiniLM
`embedding` so we can backfill BGE-M3 vectors + A/B them without touching serving retrieval.
Retrieval flips to embedding_v2 only when embed_v2_active=true. Reversible: drop this column to
roll back. See docs/EMBEDDER_LATECHUNK_SCOPE.md.

Revision ID: 0094_embedding_v2
Revises: 0093_chunk_disabled
Create Date: 2026-07-04
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = "0094_embedding_v2"
down_revision: Union[str, Sequence[str], None] = "0093_chunk_disabled"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("document_chunks", sa.Column("embedding_v2", Vector(1024), nullable=True))
    # HNSW cosine index — only covers rows where embedding_v2 is populated (partial), so it costs
    # nothing until the backfill runs and doesn't disturb the live `embedding` index.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_document_chunks_embedding_v2_hnsw "
        "ON document_chunks USING hnsw (embedding_v2 vector_cosine_ops) "
        "WHERE embedding_v2 IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_document_chunks_embedding_v2_hnsw")
    op.drop_column("document_chunks", "embedding_v2")
