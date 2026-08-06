"""line_map

Revision ID: 0106_line_map
Revises: 0105_retrieval_metrics
Create Date: 2026-07-29 00:00:00.000000

Add line_map (JSONB) to documents and line_ids (JSONB) to document_chunks.
These carry per-line geometry through the pipeline so chunk bboxes are
computed as a union of line bands rather than post-hoc word-matching.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


# revision identifiers, used by Alembic.
revision: str = '0106_line_map'
down_revision: Union[str, Sequence[str], None] = '0105_retrieval_metrics'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('documents', sa.Column('line_map', JSONB, nullable=True))
    op.add_column('document_chunks', sa.Column('line_ids', JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column('document_chunks', 'line_ids')
    op.drop_column('documents', 'line_map')
