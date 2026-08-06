"""M29 · soft-archive for documents.

Once an audit closes, hard-deleting a document referenced by its
requirements would break history snapshots and the next-cycle clone
(which inherits requirements.doc_id_external from the closed cycle).
Soft-archive hides the doc from default lists but keeps every row +
S3 object intact for posterity. Hard-delete is still allowed while the
doc is only referenced by ACTIVE audits — the policy lives in the
DELETE endpoint, not the schema.

Columns mirror Vendor.is_archived (which uses the same pattern).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0029_document_archive"
down_revision: Union[str, Sequence[str], None] = "0028_custom_categories"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column("is_archived", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column("documents", sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("documents", sa.Column("archived_by", sa.String(length=256), nullable=True))


def downgrade() -> None:
    op.drop_column("documents", "archived_by")
    op.drop_column("documents", "archived_at")
    op.drop_column("documents", "is_archived")
