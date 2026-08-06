"""M44.P4 · document_artifacts · persistent doc memory.

One row per document. Holds the materialized artifacts (markdown / JSON /
summary / entities / TOC) that are generated ONCE at ingest and served
from DB on every subsequent request. Replaces the per-request re-
generation pattern that was failing on long PDFs (markdown timed out,
JSON truncated, chat couldn't load).

Strategy column tracks which tier the doc fell into:
  · full          ≤ 20 pages   → all artifacts
  · reduced      21-50 pages   → markdown + summary + entities (no JSON)
  · summary_only 51-150 pages  → summary + TOC + entities (no markdown)
  · skipped     > 150 pages    → short summary only

Sized in line with Claude / Gemini's attachment context windows so the
"full" tier can be loaded into the chat context whole (Claude-style).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0041_document_artifacts"
down_revision: Union[str, Sequence[str], None] = "0040_agent_traces"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "document_artifacts",
        sa.Column("pk", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=False, index=True),
        sa.Column(
            "document_pk", sa.Integer(),
            sa.ForeignKey("documents.pk", ondelete="CASCADE"),
            nullable=False, unique=True,
        ),
        # Strategy chosen at materialization time. See module docstring.
        sa.Column("processing_strategy", sa.String(length=32), nullable=False),
        sa.Column("processing_notes", sa.Text(), nullable=True),
        # The artifacts themselves. Nullable per-strategy (e.g. markdown
        # null for `summary_only`; structured_json null for everything
        # past `full`).
        sa.Column("full_text_md", sa.Text(), nullable=True),
        sa.Column("summary_short", sa.Text(), nullable=True),
        sa.Column("summary_long", sa.Text(), nullable=True),
        sa.Column("structured_json", JSONB(), nullable=True),
        sa.Column("key_entities", JSONB(), nullable=True),
        sa.Column("table_of_contents", JSONB(), nullable=True),
        # Telemetry · which model produced these + how big it is
        sa.Column("model_used", sa.String(length=128), nullable=True),
        sa.Column("token_count", sa.Integer(), nullable=True),
        sa.Column("char_count", sa.Integer(), nullable=True),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), onupdate=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_document_artifacts_tenant_doc",
        "document_artifacts",
        ["tenant_id", "document_pk"],
    )


def downgrade() -> None:
    op.drop_index("ix_document_artifacts_tenant_doc", table_name="document_artifacts")
    op.drop_table("document_artifacts")
