"""users + user_roles — auth identity tied to a tenant

Revision ID: 0002_users_and_roles
Revises: 0001_initial
Create Date: 2026-05-12 12:00:00

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002_users_and_roles"
down_revision: Union[str, Sequence[str], None] = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("pk", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("email", sa.String(256), nullable=False),
        sa.Column("name", sa.String(256), nullable=False),
        # NULL for OIDC-only users (Google login); set only for dev-mode users.
        sa.Column("password_hash", sa.String(256), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("tenant_id", "email"),
    )
    op.create_index("ix_users_tenant_id", "users", ["tenant_id"])

    op.create_table(
        "user_roles",
        sa.Column("pk", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("user_pk", sa.Integer, sa.ForeignKey("users.pk", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(32), nullable=False),
        sa.UniqueConstraint("user_pk", "role"),
    )


def downgrade() -> None:
    op.drop_table("user_roles")
    op.drop_index("ix_users_tenant_id", table_name="users")
    op.drop_table("users")
