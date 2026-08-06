"""M51 · per-document tags (user-applied labels).

Adds a nullable JSONB `tags` column (list of strings) to `documents`. Set via
the workspace assistant's set_tags tool (and future UI); used for filtering.
Additive + nullable — no backfill, safe on existing rows.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0069_document_tags"
down_revision: Union[str, Sequence[str], None] = "0068_encrypt_connector_tokens"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("tags", JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column("documents", "tags")
