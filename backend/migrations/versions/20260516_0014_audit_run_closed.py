"""audit_runs.closed_at

Marks an audit as final-signoff. Set by the new POST /api/audit-runs/{id}/close
endpoint, which also writes a corresponding `audit_history` row computed
from the per-requirement verdicts.

The repo `list_all` / `get` filter audits with non-NULL `closed_at` out so
the Dashboard / Vendor Portal / Reviewer queue only show in-progress work.
NULL = active (the default).

Why a column on audit_runs (vs deleting + only keeping audit_history)? The
audit_run row keeps its requirement join + chat + RFI history intact for
forensics / re-open; audit_history is the dashboard-facing summary. Same
two-table pattern most audit firms use (working file vs final ledger).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0014_audit_run_closed"
down_revision: Union[str, Sequence[str], None] = "0013_verdict_reason_submitted"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "audit_runs",
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("audit_runs", "closed_at")
