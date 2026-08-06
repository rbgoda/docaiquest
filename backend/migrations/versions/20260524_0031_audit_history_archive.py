"""M30.7 · move soft-archive flag from audit_runs → audit_history.

Bug fix on M30.6 (0030_audit_run_archive): legacy closed audits in the
seed data only have an audit_history row (no audit_runs counterpart), so
storing is_archived on audit_runs meant the archive endpoint 404'd on
those rows. audit_history always exists for closed audits, so the flag
belongs there.

The 0030 audit_runs.is_archived columns stay (harmless, unused for now).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0031_audit_history_archive"
down_revision: Union[str, Sequence[str], None] = "0030_audit_run_archive"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "audit_history",
        sa.Column("is_archived", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column("audit_history", sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("audit_history", sa.Column("archived_by", sa.String(length=256), nullable=True))


def downgrade() -> None:
    op.drop_column("audit_history", "archived_by")
    op.drop_column("audit_history", "archived_at")
    op.drop_column("audit_history", "is_archived")
