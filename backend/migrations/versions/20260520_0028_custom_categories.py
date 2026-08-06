"""M28.5 · custom_categories · per-tenant + per-vendor category vocab.

Lets reviewers add vendor-local categories (e.g. Acme Inc starts using
"Stripe Fees" for their payment processor) and admins add firm-wide
globals. Categorizer's prompt enum is dynamically merged from canonical
(hardcoded in app/agents/categorizer.py) + global + vendor-local rows.

Scope rules:
  - scope='global'  → vendor_pk IS NULL, applies to every vendor in tenant
  - scope='vendor'  → vendor_pk NOT NULL, applies only to docs with matching vendorPk

The unique constraint allows the same name across scopes — a vendor-local
"Subscriptions" and a global "Subscriptions" can coexist (the vendor row
wins when the doc has that vendor_pk).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0028_custom_categories"
down_revision: Union[str, Sequence[str], None] = "0027_review_metadata"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "custom_categories",
        sa.Column("pk", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(64), nullable=False, index=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("mode", sa.String(16), nullable=False),  # 'expense' | 'income'
        sa.Column("scope", sa.String(16), nullable=False),  # 'global' | 'vendor'
        sa.Column(
            "vendor_pk", sa.Integer,
            sa.ForeignKey("vendors.pk", ondelete="CASCADE"),
            nullable=True, index=True,
        ),
        sa.Column("created_by", sa.String(256), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"), nullable=False,
        ),
        sa.CheckConstraint(
            "(scope = 'global' AND vendor_pk IS NULL) OR (scope = 'vendor' AND vendor_pk IS NOT NULL)",
            name="custom_categories_scope_vendor_consistency",
        ),
        sa.CheckConstraint("mode IN ('expense', 'income')", name="custom_categories_mode_enum"),
        sa.CheckConstraint("scope IN ('global', 'vendor')", name="custom_categories_scope_enum"),
        sa.UniqueConstraint(
            "tenant_id", "mode", "scope", "vendor_pk", "name",
            name="uq_custom_categories_per_scope",
        ),
    )


def downgrade() -> None:
    op.drop_table("custom_categories")
