"""merchant_category_cache · per-tenant cache of merchant→category mappings

The categorizer agent (app/agents/categorizer.py) categorizes every
transaction in an uploaded bank/CC statement. To avoid re-paying for the
same merchant on every upload (e.g. "APPLE.COM/BILL" appears on every
month's statement), we cache the canonical-form→category at the tenant
level. Cache lookup is O(1) by (tenant_id, merchant_canon), and the LLM
is only consulted for merchants we haven't seen.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0024_merchant_category_cache"
down_revision: Union[str, Sequence[str], None] = "0023_graph_layer"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "merchant_category_cache",
        sa.Column("pk", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(64), nullable=False, index=True),
        sa.Column("merchant_canon", sa.String(256), nullable=False),
        sa.Column("category", sa.String(64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("tenant_id", "merchant_canon", name="uq_merchant_cat_per_tenant"),
    )


def downgrade() -> None:
    op.drop_table("merchant_category_cache")
