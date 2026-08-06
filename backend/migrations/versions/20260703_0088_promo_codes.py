"""promo_codes + users.plan_expires_at — time-limited paid-plan grants

Superadmin creates shareable codes; redeeming one sets the user's plan + plan_expires_at
(now + duration_days). effective_plan reverts pro/enterprise to 'free' once past the expiry.

Revision ID: 0088_promo_codes
Revises: 0087_document_text_overrides
Create Date: 2026-07-03
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0088_promo_codes"
down_revision: Union[str, Sequence[str], None] = "0087_document_text_overrides"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("plan_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.create_table(
        "promo_codes",
        sa.Column("pk", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=False, index=True),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("plan", sa.String(length=16), nullable=False),
        sa.Column("duration_days", sa.Integer(), nullable=False),
        sa.Column("max_redemptions", sa.Integer(), nullable=True),
        sa.Column("redemptions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(length=256), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("tenant_id", "code", name="uq_promo_tenant_code"),
    )


def downgrade() -> None:
    op.drop_table("promo_codes")
    op.drop_column("users", "plan_expires_at")
