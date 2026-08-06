"""vendors.is_archived + archived_at + archived_by

Owner-only soft-delete for vendors. Adding a vendor archive (rather than
hard-delete) keeps audit-run history, RFI threads, and vendor-user
bindings intact — archived vendors disappear from default lists but the
historical record stays.

Backfill: existing rows get is_archived=false via the column default.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0017_vendor_archived"
down_revision: Union[str, Sequence[str], None] = "0016_audit_run_frameworks"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "vendors",
        sa.Column("is_archived", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "vendors",
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "vendors",
        sa.Column("archived_by", sa.String(length=256), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("vendors", "archived_by")
    op.drop_column("vendors", "archived_at")
    op.drop_column("vendors", "is_archived")
