"""M30.6 · soft-archive for audit runs.

Hard-deleting a closed audit destroys the entire compliance trail
(audit_history snapshot + per-requirement verdict slate + RFI thread +
the report endpoint). For an audit firm this is dangerous — regulators
or customers may demand proof a vendor was audited months/years later.

Archive hides the audit_run + its history snapshot from default lists
but preserves every row. Backed-out via /unarchive. Mirror of M29
document-archive and existing Vendor.is_archived pattern.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0030_audit_run_archive"
down_revision: Union[str, Sequence[str], None] = "0029_document_archive"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "audit_runs",
        sa.Column("is_archived", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column("audit_runs", sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("audit_runs", sa.Column("archived_by", sa.String(length=256), nullable=True))


def downgrade() -> None:
    op.drop_column("audit_runs", "archived_by")
    op.drop_column("audit_runs", "archived_at")
    op.drop_column("audit_runs", "is_archived")
