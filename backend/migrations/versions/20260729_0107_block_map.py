"""block_map

Revision ID: 0107_block_map
Revises: 0106_line_map
Create Date: 2026-07-29 00:00:00.000000

Add block_map (JSONB) to documents and block_ids (JSONB) to document_chunks.
block_map carries per-block geometry + type from the parser IR so
chunks (and later the three-pane sync) can reference stable block
identities with their bboxes — no post-hoc word-matching needed.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


# revision identifiers, used by Alembic.
revision: str = '0107_block_map'
down_revision: Union[str, Sequence[str], None] = '0106_line_map'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('documents', sa.Column('block_map', JSONB, nullable=True))
    op.add_column('document_chunks', sa.Column('block_ids', JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column('document_chunks', 'block_ids')
    op.drop_column('documents', 'block_map')
