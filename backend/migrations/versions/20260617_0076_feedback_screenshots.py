"""add product_feedback.screenshots

Revision ID: 0076_feedback_screenshots
Revises: 0075_product_feedback
Create Date: 2026-06-17

Additive, nullable: up to 3 client-compressed JPEG data URLs attached to a
piece of product feedback.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0076_feedback_screenshots"
down_revision: Union[str, Sequence[str], None] = "0075_product_feedback"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "product_feedback",
        sa.Column("screenshots", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("product_feedback", "screenshots")
