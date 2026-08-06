"""product feedback (app-level 'Send feedback' screen)

Revision ID: 0075_product_feedback
Revises: 0074_ocr_quality
Create Date: 2026-06-17

Additive: new table only. Stores app-level product feedback (rating + category +
comments + suggestion) reviewed/resolved in the superadmin console.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0075_product_feedback"
down_revision: Union[str, Sequence[str], None] = "0074_ocr_quality"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "product_feedback",
        sa.Column("pk", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("owner_user_id", sa.Integer(), nullable=True),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("rating", sa.Integer(), nullable=True),
        sa.Column("category", sa.String(length=16), nullable=False, server_default="general"),
        sa.Column("comments", sa.Text(), nullable=True),
        sa.Column("suggestion", sa.Text(), nullable=True),
        sa.Column("page", sa.String(length=64), nullable=True),
        sa.Column("app_version", sa.String(length=32), nullable=True),
        sa.Column("device_info", sa.String(length=255), nullable=True),
        sa.Column("has_issues", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="new"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_product_feedback_tenant_id", "product_feedback", ["tenant_id"])
    op.create_index("ix_product_feedback_owner_user_id", "product_feedback", ["owner_user_id"])
    op.create_index("ix_product_feedback_status", "product_feedback", ["status"])


def downgrade() -> None:
    op.drop_index("ix_product_feedback_status", table_name="product_feedback")
    op.drop_index("ix_product_feedback_owner_user_id", table_name="product_feedback")
    op.drop_index("ix_product_feedback_tenant_id", table_name="product_feedback")
    op.drop_table("product_feedback")
