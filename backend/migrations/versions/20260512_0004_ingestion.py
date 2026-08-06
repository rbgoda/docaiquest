"""ingestion: pgvector ext + documents.ingestion_status + document_chunks

Revision ID: 0004_ingestion
Revises: 0003_document_storage
Create Date: 2026-05-12 16:00:00

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

from app.config import get_settings

revision: str = "0004_ingestion"
down_revision: Union[str, Sequence[str], None] = "0003_document_storage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Postgres extension. Idempotent.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.add_column("documents", sa.Column("ingestion_status", sa.String(32), nullable=True))
    op.add_column("documents", sa.Column("ingestion_error", sa.Text, nullable=True))

    dim = get_settings().embed_dim
    op.create_table(
        "document_chunks",
        sa.Column("pk", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("document_pk", sa.Integer, sa.ForeignKey("documents.pk", ondelete="CASCADE"), nullable=False),
        sa.Column("chunk_index", sa.Integer, nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("page", sa.Integer, nullable=False),
        sa.Column("char_start", sa.Integer, nullable=False),
        sa.Column("char_end", sa.Integer, nullable=False),
        sa.Column("embedding", Vector(dim), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_document_chunks_tenant_id", "document_chunks", ["tenant_id"])
    op.create_index("ix_document_chunks_document_pk", "document_chunks", ["document_pk"])
    # HNSW index for cosine search lands in M8 once we know embeddings exist
    # at non-trivial scale. Pure-pgvector + seq scan is fine for M7 sizes.


def downgrade() -> None:
    op.drop_index("ix_document_chunks_document_pk", table_name="document_chunks")
    op.drop_index("ix_document_chunks_tenant_id", table_name="document_chunks")
    op.drop_table("document_chunks")
    op.drop_column("documents", "ingestion_error")
    op.drop_column("documents", "ingestion_status")
    op.execute("DROP EXTENSION IF EXISTS vector")
