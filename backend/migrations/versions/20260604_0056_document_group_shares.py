"""M46 · document_group_shares — a doc can be shared into MANY groups.

Replaces the single documents.group_id FK with a join table so the owner can
pick several groups via checkboxes. Backfills existing single-group memberships
into the join table. documents.group_id is kept (deprecated, no longer written)
to avoid a destructive drop; reads go through document_group_shares.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0056_document_group_shares"
down_revision: Union[str, Sequence[str], None] = "0055_document_groups"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "document_group_shares",
        sa.Column("pk", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("document_pk", sa.Integer(),
                  sa.ForeignKey("documents.pk", ondelete="CASCADE"), nullable=False),
        sa.Column("group_id", sa.Integer(),
                  sa.ForeignKey("document_groups.pk", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("document_pk", "group_id", name="uq_doc_group_share"),
    )
    op.create_index("ix_document_group_shares_tenant_id", "document_group_shares", ["tenant_id"])
    op.create_index("ix_document_group_shares_document_pk", "document_group_shares", ["document_pk"])
    op.create_index("ix_document_group_shares_group_id", "document_group_shares", ["group_id"])

    # Backfill: every doc currently in a group becomes one share row.
    op.execute(
        """
        INSERT INTO document_group_shares (tenant_id, document_pk, group_id, created_at)
        SELECT tenant_id, pk, group_id, now()
        FROM documents
        WHERE group_id IS NOT NULL
        """
    )


def downgrade() -> None:
    op.drop_table("document_group_shares")
