"""documents: cached vision-rendered Markdown (rendered_markdown + at + model)

Faithful whole-document Markdown is rendered on-demand by a vision model (qwen-vl) per page and
cached here so it's built once per document.

Revision ID: 0092_rendered_markdown
Revises: 0091_feedback_ref_verified
Create Date: 2026-07-04
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0092_rendered_markdown"
down_revision: Union[str, Sequence[str], None] = "0091_feedback_ref_verified"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("rendered_markdown", sa.Text(), nullable=True))
    op.add_column("documents", sa.Column("rendered_markdown_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("documents", sa.Column("rendered_markdown_model", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("documents", "rendered_markdown_model")
    op.drop_column("documents", "rendered_markdown_at")
    op.drop_column("documents", "rendered_markdown")
