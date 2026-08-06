"""M42 · access-request gate · per-tenant user freeze flag.

Adds `users.is_frozen` to gate login at the tenant level. The cp_db
already has `companies.is_frozen` (cp migration 0008); this mirror on
the tenant side lets the per-tenant backend enforce freeze without an
HTTP round-trip to the control plane on every login.

Backfill policy:
  * Shared free SaaS container (tenant_id = '__shared__') — set every
    existing user's is_frozen = TRUE. Existing free workspaces become
    unreachable until the user re-submits an access request and the
    superadmin approves.
  * All other tenants (paid, dedicated containers) — column defaults to
    FALSE on every row. Paid customers are NOT disrupted.

Runs idempotently per tenant on `alembic upgrade head` at container boot.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0037_user_freeze"
down_revision: Union[str, Sequence[str], None] = "0036_tenant_plan_limits"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("is_frozen", sa.Boolean(), nullable=False, server_default="false"),
    )
    # Only backfill the shared free tenant. Paid tenants stay live.
    op.execute("UPDATE users SET is_frozen = TRUE WHERE tenant_id = '__shared__'")


def downgrade() -> None:
    op.drop_column("users", "is_frozen")
