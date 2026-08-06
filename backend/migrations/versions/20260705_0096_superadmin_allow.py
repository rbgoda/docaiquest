"""superadmin_allow — DB-backed admin allowlist (managed from the console)

Revision ID: 0096_superadmin_allow
Revises: 0095_qa_result
Create Date: 2026-07-05
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0096_superadmin_allow"
down_revision: Union[str, Sequence[str], None] = "0095_qa_result"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "superadmin_allow",
        sa.Column("pk", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(64), nullable=False, index=True),
        sa.Column("email", sa.String(256), nullable=False),
        sa.Column("added_by", sa.String(256), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("tenant_id", "email", name="uq_superadmin_allow_tenant_email"),
    )


def downgrade() -> None:
    op.drop_table("superadmin_allow")
