"""M43.P1.A · Add context_summary to document_chunks.

Stores the ~50-100 token Anthropic-style situating context generated for
each chunk during ingestion. The context is prepended to the chunk text
before embedding (Contextual Retrieval pattern) — which roughly halves
the "retrieval missed it" error rate.

Existing chunks get NULL — they continue to work with hybrid retrieval
unchanged, just without the contextual boost. Re-ingesting a document
populates context_summary on its chunks.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0038_chunk_context"
down_revision: Union[str, Sequence[str], None] = "0037_user_freeze"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "document_chunks",
        sa.Column("context_summary", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("document_chunks", "context_summary")
