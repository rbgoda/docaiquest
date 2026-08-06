"""add_pipeline_version_to_chunks

Revision ID: 0110_add_pipeline_version_to_chunks
Revises: 0109_custom_llm_providers
Create Date: 2026-08-06 00:00:00.000000

Add pipeline_version column to document_chunks — tracks which pipeline version
produced each chunk (for future re-chunking / reprocessing decisions).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0110_pipeline_version"
down_revision: Union[str, None] = "0109_custom_llm_providers"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "document_chunks",
        sa.Column("pipeline_version", sa.Integer(), nullable=False, server_default="1"),
    )


def downgrade() -> None:
    op.drop_column("document_chunks", "pipeline_version")
