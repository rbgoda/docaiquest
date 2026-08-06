"""verdict_reason + audit_runs.vendor_submitted_at

Two related additions to close the reviewer↔vendor feedback loop:

  * `audit_run_requirements.verdict_reason` — free-text reason the reviewer
    enters when rejecting or requesting info. Optional on approve. The
    vendor sees this verbatim on their VendorHome row so they know what to
    fix; without it, "Rejected" is opaque and the vendor either guesses
    or has to chase the reviewer by email.

  * `audit_runs.vendor_submitted_at` — set when the vendor clicks
    "Submit for review" in VendorHome. Reviewer dashboards filter on
    this so the reviewer knows which audits actually have evidence
    queued for them vs which are still being assembled by the vendor.
    NULL = vendor still working; non-NULL = vendor declared ready.

Both nullable so existing rows stay valid without a backfill.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0013_verdict_reason_submitted"
down_revision: Union[str, Sequence[str], None] = "0012_framework_packs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "audit_run_requirements",
        sa.Column("verdict_reason", sa.Text, nullable=True),
    )
    op.add_column(
        "audit_runs",
        sa.Column("vendor_submitted_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("audit_runs", "vendor_submitted_at")
    op.drop_column("audit_run_requirements", "verdict_reason")
