"""P9.5 · document_chunks.kind · discriminate table chunks from text chunks.

P9.5 (table-preserving extraction) renders PDF tables to Markdown and stores
them as chunks. They need to be told apart from ordinary sliding-window text
chunks so the materializer can preserve them verbatim. `kind` defaults to
'text' so every existing chunk keeps its current meaning with no backfill.

Additive + server_default → zero behaviour change for existing rows.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0044_chunk_kind"
down_revision: Union[str, Sequence[str], None] = "0043_chat_workspace_key"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "document_chunks",
        sa.Column(
            "kind", sa.String(length=16), nullable=False, server_default="text"
        ),
    )


def downgrade() -> None:
    op.drop_column("document_chunks", "kind")
