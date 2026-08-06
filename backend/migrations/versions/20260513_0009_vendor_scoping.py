"""vendor_scoping (M17 phase 1)

Add nullable vendor_pk FK columns to documents, requirement_rfis, and users.
This is the schema-only phase: every column is NULLABLE and no repository
filtering is in place yet. Existing rows are untouched (vendor_pk stays
NULL). Phase 2 will populate the columns and add the repo filter pass.

Revision ID: 0009_vendor_scoping
Revises: 0008_requirement_rfis
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0009_vendor_scoping"
down_revision: Union[str, Sequence[str], None] = "0008_requirement_rfis"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Each FK uses ondelete=SET NULL so removing a vendor doesn't cascade
    # into deleting docs / RFIs / users — preserves audit trail. Phase 2
    # will tighten this once we're sure of the user flows.
    op.add_column(
        "documents",
        sa.Column("vendor_pk", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_documents_vendor_pk",
        "documents", "vendors",
        ["vendor_pk"], ["pk"],
        ondelete="SET NULL",
    )
    op.create_index("ix_documents_vendor_pk", "documents", ["vendor_pk"])

    op.add_column(
        "requirement_rfis",
        sa.Column("vendor_pk", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_requirement_rfis_vendor_pk",
        "requirement_rfis", "vendors",
        ["vendor_pk"], ["pk"],
        ondelete="SET NULL",
    )
    op.create_index("ix_requirement_rfis_vendor_pk", "requirement_rfis", ["vendor_pk"])

    op.add_column(
        "users",
        sa.Column("vendor_pk", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_users_vendor_pk",
        "users", "vendors",
        ["vendor_pk"], ["pk"],
        ondelete="SET NULL",
    )
    op.create_index("ix_users_vendor_pk", "users", ["vendor_pk"])


def downgrade() -> None:
    op.drop_index("ix_users_vendor_pk", table_name="users")
    op.drop_constraint("fk_users_vendor_pk", "users", type_="foreignkey")
    op.drop_column("users", "vendor_pk")

    op.drop_index("ix_requirement_rfis_vendor_pk", table_name="requirement_rfis")
    op.drop_constraint("fk_requirement_rfis_vendor_pk", "requirement_rfis", type_="foreignkey")
    op.drop_column("requirement_rfis", "vendor_pk")

    op.drop_index("ix_documents_vendor_pk", table_name="documents")
    op.drop_constraint("fk_documents_vendor_pk", "documents", type_="foreignkey")
    op.drop_column("documents", "vendor_pk")
