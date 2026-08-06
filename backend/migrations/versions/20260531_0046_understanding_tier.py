"""M44.P10 PR1 · local "understanding" tier (additive scaffolding).

Foundation for both delete-with-learning (P10) and federated learning (P13).
Adds the three tenant-level UNDERSTANDING tables + the discriminator columns,
all additive with safe defaults so running workers are unaffected and there's
no backfill. The promotion engine (P10 PR2) and the global pipeline (P13)
write to these later; today they sit empty.

  · extraction_corrections — anonymized "LLM often misses field Y on type X"
  · agent_skill_memory      — tool sequences that win for a doc_type
  · entity_canonical        — org/person canonicals + aliases (LOCAL ONLY —
                              real names are customer data, never promoted)
  · reflexion_pairs.kind    — 'doc_specific' (purge on delete) | 'general' (keep)
  · documents.deletion_status — two-phase delete marker

`source` ('local' | 'global') on the two promotable tables lets us tell
earned-vs-seeded knowledge apart so seeded global rows never get re-promoted
(no echo loop). entity_canonical has no `source` — it never leaves the tenant.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0046_understanding_tier"
down_revision: Union[str, Sequence[str], None] = "0045_pii_vault"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "extraction_corrections",
        sa.Column("pk", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=False, index=True),
        sa.Column("doc_type", sa.String(length=64), nullable=False),
        sa.Column("pattern_kind", sa.String(length=32), nullable=False),
        sa.Column("pattern", JSONB(), nullable=False),
        sa.Column("observations_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("source", sa.String(length=8), nullable=False, server_default="local"),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_corrections_lookup", "extraction_corrections", ["tenant_id", "doc_type"])
    # Functional unique index — dedup the promotion engine's upserts on the
    # mismatched field. Expression indexes can't be a table-level
    # UniqueConstraint in SQLAlchemy, so create it as a unique index here.
    op.create_index(
        "uq_corrections",
        "extraction_corrections",
        ["tenant_id", "doc_type", "pattern_kind", sa.text("(pattern->>'wrong_field')")],
        unique=True,
    )

    op.create_table(
        "agent_skill_memory",
        sa.Column("pk", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=False, index=True),
        sa.Column("doc_type", sa.String(length=64), nullable=False),
        sa.Column("question_template", sa.Text(), nullable=False),
        sa.Column("tool_sequence", JSONB(), nullable=False),
        sa.Column("success_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("failure_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("source", sa.String(length=8), nullable=False, server_default="local"),
        sa.Column("last_used_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_skill_lookup", "agent_skill_memory", ["tenant_id", "doc_type"])

    op.create_table(
        "entity_canonical",
        sa.Column("pk", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=False, index=True),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("canonical", sa.String(length=256), nullable=False),
        sa.Column("aliases", JSONB(), nullable=False, server_default="[]"),
        sa.Column("observed_count", sa.Integer(), nullable=False, server_default="1"),
        sa.UniqueConstraint("tenant_id", "kind", "canonical", name="uq_canonical"),
    )

    op.add_column(
        "reflexion_pairs",
        sa.Column("kind", sa.String(length=16), nullable=False, server_default="doc_specific"),
    )
    op.add_column(
        "documents",
        sa.Column("deletion_status", sa.String(length=16), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("documents", "deletion_status")
    op.drop_column("reflexion_pairs", "kind")
    op.drop_table("entity_canonical")
    op.drop_index("ix_skill_lookup", table_name="agent_skill_memory")
    op.drop_table("agent_skill_memory")
    op.drop_index("uq_corrections", table_name="extraction_corrections")
    op.drop_index("ix_corrections_lookup", table_name="extraction_corrections")
    op.drop_table("extraction_corrections")
