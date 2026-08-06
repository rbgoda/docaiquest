"""Graph RAG layer (L3.1) — entity graph schema

Adds the foundation for Layer 3 of the structured-facts → RAG → Graph stack:

1. `entities` table is extended (back-compat: new columns are nullable):
   - vendor_pk (FK, denormalized for fast per-vendor filter)
   - canonical (normalized name for dedup + fuzzy match)
   - source (varchar — 'regex' | 'fact_bootstrap' | 'llm_entity')
   - graph_run_pk (FK to graph_runs — which extraction pass wrote this row)
   - confidence (FLOAT — extraction confidence, NULL for regex)

2. `entity_relations` (new) — edges between entities.
   src_entity_pk → dst_entity_pk with a typed relation string. Each edge
   carries vendor_pk denormalized (matches both endpoints' vendor for
   intra-vendor edges; cross-vendor edges are allowed but rare and have
   a metadata hint). evidence_chunk_pk + evidence_doc_pk let the UI
   trace any edge back to its source span.

3. `graph_runs` (new) — audit/history of extraction passes.
   Every entity / relation insert is tagged with the run that produced
   it, so re-running an extraction can tear down only that run's rows
   without touching others. Status flow: pending → running → complete/failed.

Indexes:
  entities          (tenant_id, vendor_pk, kind, canonical)
  entities          (tenant_id, vendor_pk, document_pk)
  entity_relations  (tenant_id, vendor_pk, relation)
  entity_relations  (src_entity_pk, relation, dst_entity_pk)
  entity_relations  (dst_entity_pk, relation, src_entity_pk)  -- reverse walks
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB


revision: str = "0023_graph_layer"
down_revision: Union[str, Sequence[str], None] = "0022_doc_chat_bbox"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. graph_runs — must exist before entities can FK to it.
    op.create_table(
        "graph_runs",
        sa.Column("pk", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(64), nullable=False, index=True),
        sa.Column(
            "document_pk",
            sa.Integer,
            sa.ForeignKey("documents.pk", ondelete="CASCADE"),
            nullable=True,
            index=True,
        ),
        sa.Column("kind", sa.String(32), nullable=False),  # bootstrap | llm_entity | llm_relation | reconcile
        sa.Column("model", sa.String(128), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("entities_added", sa.Integer, nullable=False, server_default="0"),
        sa.Column("relations_added", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )

    # 2. Extend entities for graph use.
    op.add_column("entities", sa.Column(
        "vendor_pk",
        sa.Integer,
        sa.ForeignKey("vendors.pk", ondelete="SET NULL"),
        nullable=True,
    ))
    op.add_column("entities", sa.Column("canonical", sa.String(256), nullable=True))
    op.add_column("entities", sa.Column(
        "source",
        sa.String(32),
        nullable=False,
        server_default="regex",
    ))
    op.add_column("entities", sa.Column(
        "graph_run_pk",
        sa.Integer,
        sa.ForeignKey("graph_runs.pk", ondelete="SET NULL"),
        nullable=True,
    ))
    op.add_column("entities", sa.Column("confidence", sa.Float, nullable=True))

    op.create_index(
        "ix_entities_vendor_kind_canonical",
        "entities",
        ["tenant_id", "vendor_pk", "kind", "canonical"],
    )
    op.create_index(
        "ix_entities_vendor_doc",
        "entities",
        ["tenant_id", "vendor_pk", "document_pk"],
    )

    # 3. entity_relations — directed edges with provenance.
    op.create_table(
        "entity_relations",
        sa.Column("pk", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(64), nullable=False, index=True),
        sa.Column(
            "vendor_pk",
            sa.Integer,
            sa.ForeignKey("vendors.pk", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "src_entity_pk",
            sa.Integer,
            sa.ForeignKey("entities.pk", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "dst_entity_pk",
            sa.Integer,
            sa.ForeignKey("entities.pk", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("relation", sa.String(64), nullable=False),
        sa.Column("confidence", sa.Float, nullable=True),
        sa.Column(
            "evidence_doc_pk",
            sa.Integer,
            sa.ForeignKey("documents.pk", ondelete="CASCADE"),
            nullable=True,
            index=True,
        ),
        sa.Column(
            "evidence_chunk_pk",
            sa.Integer,
            sa.ForeignKey("document_chunks.pk", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("metadata_json", JSONB, nullable=True),
        sa.Column("source", sa.String(32), nullable=False, server_default="fact_bootstrap"),
        sa.Column(
            "graph_run_pk",
            sa.Integer,
            sa.ForeignKey("graph_runs.pk", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    # Hot-path indexes for graph traversal.
    op.create_index(
        "ix_entity_relations_vendor_rel",
        "entity_relations",
        ["tenant_id", "vendor_pk", "relation"],
    )
    op.create_index(
        "ix_entity_relations_src",
        "entity_relations",
        ["src_entity_pk", "relation", "dst_entity_pk"],
    )
    op.create_index(
        "ix_entity_relations_dst",
        "entity_relations",
        ["dst_entity_pk", "relation", "src_entity_pk"],
    )


def downgrade() -> None:
    op.drop_index("ix_entity_relations_dst", table_name="entity_relations")
    op.drop_index("ix_entity_relations_src", table_name="entity_relations")
    op.drop_index("ix_entity_relations_vendor_rel", table_name="entity_relations")
    op.drop_table("entity_relations")

    op.drop_index("ix_entities_vendor_doc", table_name="entities")
    op.drop_index("ix_entities_vendor_kind_canonical", table_name="entities")
    op.drop_column("entities", "confidence")
    op.drop_column("entities", "graph_run_pk")
    op.drop_column("entities", "source")
    op.drop_column("entities", "canonical")
    op.drop_column("entities", "vendor_pk")

    op.drop_table("graph_runs")
