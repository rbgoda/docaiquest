"""M46 · document groups — share individual docs to a group (shared Drive folder).

Adds document_groups + document_group_members, and a documents.group_id FK.
Additive; personal docs (group_id NULL) are unchanged.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0055_document_groups"
down_revision: Union[str, Sequence[str], None] = "0054_learned_doc_types"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "document_groups",
        sa.Column("pk", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), nullable=False),
        sa.Column("created_by_email", sa.String(length=256), nullable=True),
        sa.Column("drive_folder_id", sa.String(length=256), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_document_groups_tenant_id", "document_groups", ["tenant_id"])
    op.create_index("ix_document_groups_created_by_user_id", "document_groups", ["created_by_user_id"])

    op.create_table(
        "document_group_members",
        sa.Column("pk", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("group_id", sa.Integer(),
                  sa.ForeignKey("document_groups.pk", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("member_email", sa.String(length=256), nullable=False),
        sa.Column("role", sa.String(length=16), server_default="member", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("group_id", "member_email", name="uq_group_member"),
    )
    op.create_index("ix_document_group_members_tenant_id", "document_group_members", ["tenant_id"])
    op.create_index("ix_document_group_members_group_id", "document_group_members", ["group_id"])
    op.create_index("ix_document_group_members_user_id", "document_group_members", ["user_id"])

    op.add_column("documents", sa.Column("group_id", sa.Integer(),
                  sa.ForeignKey("document_groups.pk", ondelete="SET NULL"), nullable=True))
    op.create_index("ix_documents_group_id", "documents", ["group_id"])


def downgrade() -> None:
    op.drop_index("ix_documents_group_id", table_name="documents")
    op.drop_column("documents", "group_id")
    op.drop_table("document_group_members")
    op.drop_table("document_groups")
