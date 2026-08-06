"""G3 · add documents.ocr_quality (page-level OCR quality summary)

Revision ID: 0074_ocr_quality
Revises: 0073_backup_encryption
Create Date: 2026-06-16

Additive, nullable — existing rows stay NULL until re-ingested. Holds the
summary produced by app.ocr_quality.summarize_pages for vision-OCR'd docs.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0074_ocr_quality"
down_revision: Union[str, Sequence[str], None] = "0073_backup_encryption"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column("ocr_quality", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("documents", "ocr_quality")
