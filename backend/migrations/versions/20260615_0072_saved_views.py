"""Intelligence Dashboard · saved / AI-proposed views (standalone port).

Persists per-user view specs: AI-proposed views (`source='ai'`) and user pins/
edits (`source='user'`). Built-in views stay code-defined. Owner-scoped.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0072_saved_views"
down_revision: Union[str, Sequence[str], None] = "0071_api_client_groups"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "saved_views",
        sa.Column("pk", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("owner_user_id", sa.Integer(), nullable=False),
        sa.Column("view_key", sa.String(length=64), nullable=False),
        sa.Column("spec", postgresql.JSONB(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False, server_default="ai"),
        sa.Column("pinned", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("dismissed", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("tenant_id", "owner_user_id", "view_key", name="uq_saved_views_owner_key"),
    )
    op.create_index("ix_saved_views_tenant", "saved_views", ["tenant_id"])
    op.create_index("ix_saved_views_owner", "saved_views", ["owner_user_id"])


def downgrade() -> None:
    op.drop_index("ix_saved_views_owner", table_name="saved_views")
    op.drop_index("ix_saved_views_tenant", table_name="saved_views")
    op.drop_table("saved_views")
