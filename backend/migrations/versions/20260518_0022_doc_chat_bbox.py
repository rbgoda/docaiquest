"""document_chunks.bbox + chat_messages.doc_id_external (M11.7)

Two additions, both nullable for back-compat:

1. document_chunks.bbox jsonb — per-chunk bounding box on its page.
   Shape: {"page": int, "x0": float, "y0": float, "x1": float, "y1": float}.
   Populated at ingestion time via PyMuPDF page.search_for(); NULL when
   the chunk text isn't findable on the page (rare — happens with chunks
   straddling page boundaries or after heavy normalization).

2. chat_messages.doc_id_external — when set, this message belongs to a
   document-scoped chat thread (M11.7 chat-with-document feature) rather
   than the requirement-scoped chat (M2). Either column is the thread
   anchor, never both. The existing requirement_id_external NOT NULL
   constraint is relaxed to allow doc-scoped rows.

3. chat_messages.citations jsonb — when present, lists the chunk_pks the
   AI cited for its answer. Frontend uses these to render yellow bbox
   markers on the linked PDF.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB


revision: str = "0022_doc_chat_bbox"
down_revision: Union[str, Sequence[str], None] = "0021_document_classification"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Bbox on chunks
    op.add_column("document_chunks", sa.Column("bbox", JSONB, nullable=True))

    # 2. Doc-scoped chat: relax NOT NULL on requirement_id_external, add
    #    doc_id_external + citations
    op.alter_column(
        "chat_messages",
        "requirement_id_external",
        existing_type=sa.String(64),
        nullable=True,
    )
    op.add_column("chat_messages", sa.Column("doc_id_external", sa.String(64), nullable=True))
    op.add_column("chat_messages", sa.Column("citations", JSONB, nullable=True))
    op.create_index(
        "ix_chat_messages_doc",
        "chat_messages",
        ["tenant_id", "doc_id_external"],
    )


def downgrade() -> None:
    op.drop_index("ix_chat_messages_doc", table_name="chat_messages")
    op.drop_column("chat_messages", "citations")
    op.drop_column("chat_messages", "doc_id_external")
    op.alter_column(
        "chat_messages",
        "requirement_id_external",
        existing_type=sa.String(64),
        nullable=False,
    )
    op.drop_column("document_chunks", "bbox")
