"""retrieval: tsvector + HNSW + entities

Revision ID: 0005_retrieval
Revises: 0004_ingestion
Create Date: 2026-05-12 18:00:00

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0005_retrieval"
down_revision: Union[str, Sequence[str], None] = "0004_ingestion"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ---- BM25 (tsvector + GIN) ------------------------------------------
    # Generated column means the index stays consistent with text on write;
    # no need to remember to call to_tsvector at query time.
    op.execute(
        "ALTER TABLE document_chunks "
        "ADD COLUMN tsv tsvector GENERATED ALWAYS AS (to_tsvector('english', text)) STORED"
    )
    op.execute(
        "CREATE INDEX ix_document_chunks_tsv ON document_chunks USING GIN (tsv)"
    )

    # ---- Cosine ANN (HNSW) ----------------------------------------------
    # Defaults are reasonable for our scale; tune m / ef_construction in M11
    # if recall becomes a problem at 100k+ chunks.
    op.execute(
        "CREATE INDEX ix_document_chunks_embedding_hnsw "
        "ON document_chunks USING hnsw (embedding vector_cosine_ops)"
    )

    # ---- entities --------------------------------------------------------
    op.create_table(
        "entities",
        sa.Column("pk", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("document_pk", sa.Integer, sa.ForeignKey("documents.pk", ondelete="CASCADE"), nullable=False),
        sa.Column("chunk_pk", sa.Integer, sa.ForeignKey("document_chunks.pk", ondelete="CASCADE"), nullable=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("page", sa.Integer, nullable=False),
        sa.Column("entity_metadata", JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_entities_tenant_id", "entities", ["tenant_id"])
    op.create_index("ix_entities_document_pk", "entities", ["document_pk"])
    op.create_index("ix_entities_kind", "entities", ["tenant_id", "kind"])


def downgrade() -> None:
    op.drop_index("ix_entities_kind", table_name="entities")
    op.drop_index("ix_entities_document_pk", table_name="entities")
    op.drop_index("ix_entities_tenant_id", table_name="entities")
    op.drop_table("entities")

    op.execute("DROP INDEX IF EXISTS ix_document_chunks_embedding_hnsw")
    op.execute("DROP INDEX IF EXISTS ix_document_chunks_tsv")
    op.execute("ALTER TABLE document_chunks DROP COLUMN IF EXISTS tsv")
