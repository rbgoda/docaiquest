"""translations

Revision ID: 0108_translations
Revises: 0107_block_map
Create Date: 2026-07-31 00:00:00.000000

Add translations (JSONB) column to documents table for caching per-doc
LLM translations keyed by language code.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


# revision identifiers, used by Alembic.
revision: str = '0108_translations'
down_revision: Union[str, Sequence[str], None] = '0107_block_map'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('documents', sa.Column('translations', JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column('documents', 'translations')
