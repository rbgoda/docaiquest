"""Durable cross-document entity identities.

Graph step 3. The graph stores per-document `Entity` rows, so the same real-world
entity was fragmented across docs with no persistent node. This adds a durable
`entity_identity` table: one row per resolved cross-document identity (person/org),
derived from the per-doc mentions by the resolver and refreshed after each ingest.
It survives any single document's re-extraction (unlike the old bootstrap alias
reuse, whose shared node was owned by the first doc's graph-run and CASCADE-deleted
on that doc's re-extract).

Revision ID: 0102_entity_identity
Revises: 0101_schema_lib_drop_leaked_meta
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0102_entity_identity"
down_revision = "0101_schema_lib_drop_leaked_meta"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "entity_identity",
        sa.Column("pk", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("owner_user_id", sa.Integer, nullable=True),
        sa.Column("kind", sa.String(32), nullable=False),          # person | org
        sa.Column("identity_key", sa.String(256), nullable=False),  # stable canonical (longest)
        sa.Column("display_name", sa.String(256), nullable=False),
        sa.Column("aliases", JSONB, nullable=False, server_default="[]"),
        sa.Column("doc_pks", JSONB, nullable=False, server_default="[]"),
        sa.Column("mention_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("tenant_id", "owner_user_id", "kind", "identity_key",
                            name="uq_entity_identity"),
    )
    op.create_index("ix_entity_identity_scope", "entity_identity",
                    ["tenant_id", "owner_user_id", "kind"])


def downgrade() -> None:
    op.drop_index("ix_entity_identity_scope", table_name="entity_identity")
    op.drop_table("entity_identity")
