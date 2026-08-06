"""document storage fields + highlight bbox

Revision ID: 0003_document_storage
Revises: 0002_users_and_roles
Create Date: 2026-05-12 14:00:00

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0003_document_storage"
down_revision: Union[str, Sequence[str], None] = "0002_users_and_roles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("s3_key", sa.String(512), nullable=True))
    op.add_column("documents", sa.Column("mime_type", sa.String(128), nullable=True))
    op.add_column("documents", sa.Column("sha256", sa.String(64), nullable=True))
    op.add_column("documents", sa.Column("uploaded_by", sa.String(256), nullable=True))
    op.create_index("ix_documents_sha256", "documents", ["sha256"])

    op.add_column("highlights", sa.Column("bbox", JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column("highlights", "bbox")
    op.drop_index("ix_documents_sha256", table_name="documents")
    op.drop_column("documents", "uploaded_by")
    op.drop_column("documents", "sha256")
    op.drop_column("documents", "mime_type")
    op.drop_column("documents", "s3_key")
