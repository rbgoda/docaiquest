"""M36 · plan_type + usage counters on tenants.

Adds the columns needed for free-tier SaaS:
  plan_type             'paid' (default · per-tenant container, unchanged) or 'free'
  doc_count_this_month  ingestion counter, reset nightly
  audits_created        count of audit_runs ever made (free = max 1)
  llm_calls_this_hour   rate-limit counter
  llm_hour_window_start when the current llm hour window opened

Existing per-tenant containers keep plan_type='paid' so all enforcement
is no-op for them. The shared free container (M37) inserts new rows
with plan_type='free'.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0036_tenant_plan_limits"
down_revision: Union[str, Sequence[str], None] = "0035_evidence_docs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tenants", sa.Column("plan_type", sa.String(16), nullable=False, server_default="paid"))
    op.add_column("tenants", sa.Column("doc_count_this_month", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("tenants", sa.Column("audits_created", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("tenants", sa.Column("llm_calls_this_hour", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("tenants", sa.Column("llm_hour_window_start", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("tenants", "llm_hour_window_start")
    op.drop_column("tenants", "llm_calls_this_hour")
    op.drop_column("tenants", "audits_created")
    op.drop_column("tenants", "doc_count_this_month")
    op.drop_column("tenants", "plan_type")
