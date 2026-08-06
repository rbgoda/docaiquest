"""M46 · §compliance · documents.created_at — needed for age-based retention.

Adds a server-side creation timestamp to documents so the retention job can move
originals older than N days to Drive. Existing rows default to now() (they start
their retention clock at the migration).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0061_documents_created_at"
down_revision: Union[str, Sequence[str], None] = "0060_consent_records"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("documents", sa.Column(
        "created_at", sa.DateTime(timezone=True),
        server_default=sa.func.now(), nullable=False))
    op.create_index("ix_documents_created_at", "documents", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_documents_created_at", table_name="documents")
    op.drop_column("documents", "created_at")
