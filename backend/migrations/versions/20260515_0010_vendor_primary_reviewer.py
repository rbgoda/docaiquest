"""vendors.primary_reviewer

Add a nullable text column to `vendors` for the default lead reviewer the New
Audit Run wizard should pre-fill when creating audits for this vendor.

Not an FK to users.email — admin can name a pending invite address before the
user record exists, and we already accept that pattern in `audit_runs.lead_reviewer`.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0010_vendor_primary_reviewer"
down_revision: Union[str, Sequence[str], None] = "0009_vendor_scoping"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "vendors",
        sa.Column("primary_reviewer", sa.String(length=256), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("vendors", "primary_reviewer")
