"""M43.P1.5 · reflexion_pairs · Hermes-style self-critique memory.

One row per chat-answer that went through the Critic-Refine loop. Stores
the question + a vector of its embedding (for cosine lookup of similar
prior questions), the first-pass draft, the critique(s) raised, and the
final answer that survived. Reviewer thumbs up/down increment
`helpful_count` / `marked_unhelpful_count` so the few-shot retrieval
filter can prefer curated critiques.

The vector dimension matches `DOCAIQ_EMBED_DIM` (default 384) so we can
reuse the same embedding backend as document_chunks.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = "0039_reflexion_pairs"
down_revision: Union[str, Sequence[str], None] = "0038_chunk_context"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "reflexion_pairs",
        sa.Column("pk", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=False, index=True),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("question_embed", Vector(384), nullable=True),
        sa.Column("draft_answer", sa.Text(), nullable=False),
        sa.Column("critique", sa.Text(), nullable=True),
        sa.Column("final_answer", sa.Text(), nullable=False),
        sa.Column("doc_id_external", sa.String(length=64), nullable=True, index=True),
        # Reviewer feedback · increments via the thumbs UI
        sa.Column("helpful_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("marked_unhelpful_count", sa.Integer(), nullable=False, server_default="0"),
        # Critic-loop telemetry
        sa.Column("iterations", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("passed_on_first", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_reflexion_pairs_tenant_doc", "reflexion_pairs", ["tenant_id", "doc_id_external"])
    # HNSW index for cosine similarity over question_embed · matches the
    # pattern document_chunks.embedding uses. Skip if pgvector version
    # doesn't support HNSW (rare).
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_reflexion_pairs_embed_hnsw "
        "ON reflexion_pairs USING hnsw (question_embed vector_cosine_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_reflexion_pairs_embed_hnsw")
    op.drop_index("ix_reflexion_pairs_tenant_doc", table_name="reflexion_pairs")
    op.drop_table("reflexion_pairs")
