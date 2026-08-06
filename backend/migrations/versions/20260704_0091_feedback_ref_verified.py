"""product_feedback: ref (versioned id) + verified_by/verified_at

`ref` = "1.1.<patch>.<seq>" (patch = #resolved/verified at creation, so the main version grows
as feedback resolves). A superadmin can mark a feedback 'verified'; on verify its screenshots
are purged (row kept).

Revision ID: 0091_feedback_ref_verified
Revises: 0090_schema_library
Create Date: 2026-07-04
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0091_feedback_ref_verified"
down_revision: Union[str, Sequence[str], None] = "0090_schema_library"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("product_feedback", sa.Column("ref", sa.String(length=24), nullable=True))
    op.add_column("product_feedback", sa.Column("verified_by", sa.String(length=256), nullable=True))
    op.add_column("product_feedback", sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_product_feedback_ref", "product_feedback", ["ref"])


def downgrade() -> None:
    op.drop_index("ix_product_feedback_ref", table_name="product_feedback")
    op.drop_column("product_feedback", "verified_at")
    op.drop_column("product_feedback", "verified_by")
    op.drop_column("product_feedback", "ref")
