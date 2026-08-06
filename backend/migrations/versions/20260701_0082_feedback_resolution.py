"""add resolution note to product_feedback (auto-triage draft + manual notes)

Revision ID: 0082_feedback_resolution
Revises: 0081_document_annotations
Create Date: 2026-07-01

Auto-triage (services/feedback_triage.py) drafts a resolution and flips status to
'in_progress'; superadmins can then edit the note. One nullable Text column.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0082_feedback_resolution"
down_revision: Union[str, Sequence[str], None] = "0081_document_annotations"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("product_feedback", sa.Column("resolution", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("product_feedback", "resolution")
