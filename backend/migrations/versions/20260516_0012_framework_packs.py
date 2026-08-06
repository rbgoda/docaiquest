"""framework_packs

Per-tenant table of bulk-import packs. The six built-in packs (SOC 2, ISO
27001, HIPAA, PCI DSS v4, NIST 800-171, GDPR) continue to ship as static
files under `public/samples/frameworks/` and are NOT migrated into this
table — the frontend merges them with custom rows when rendering the grid.
This keeps re-installs/rebuilds idempotent and avoids forcing every tenant
to carry redundant copies of stable content.

Custom packs created by admins live here. We store the CSV body verbatim so:
  * the existing bulk-import endpoint can re-read it on every "Load" click
  * admins can re-export / inspect what they uploaded
  * we don't need a separate schema for control rows

`source` is reserved for a future `built_in` value if we ever migrate the
built-ins; today every row is `custom`.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB


revision: str = "0012_framework_packs"
down_revision: Union[str, Sequence[str], None] = "0011_requirement_required_docs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "framework_packs",
        sa.Column("pk", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(64), index=True, nullable=False),
        sa.Column("id_external", sa.String(128), nullable=False),  # slug, e.g. "sox-2024"
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("version", sa.String(64), nullable=True),
        sa.Column("publisher", sa.String(256), nullable=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("categories", JSONB, nullable=False, server_default="[]"),
        sa.Column("control_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("source", sa.String(16), nullable=False, server_default="custom"),
        sa.Column("csv_body", sa.Text, nullable=False),
        sa.Column("created_by", sa.String(256), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("tenant_id", "id_external", name="uq_framework_packs_tenant_slug"),
    )


def downgrade() -> None:
    op.drop_table("framework_packs")
