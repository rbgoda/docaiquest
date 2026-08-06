"""M46 · Documents System · Google Drive connector + retention.

Adds connector provenance/retention to documents (source, source_ref,
retain_original) and a connector_accounts table holding a user's connected
external source (today: Google Drive). All additive + nullable / defaulted, so
the auditing product and existing rows are unaffected (retain_original defaults
true → nothing is ever purged unless a documents connector opts in).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0049_drive_connector"
down_revision: Union[str, Sequence[str], None] = "0048_doc_owner_user_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("source", sa.String(length=16), nullable=True))
    op.add_column("documents", sa.Column("source_ref", sa.String(length=512), nullable=True))
    op.add_column(
        "documents",
        sa.Column("retain_original", sa.Boolean(), nullable=False, server_default="true"),
    )

    op.create_table(
        "connector_accounts",
        sa.Column("pk", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("owner_user_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("backend", sa.String(length=16), nullable=False),
        sa.Column("access_token", sa.String(length=2048), nullable=True),
        sa.Column("refresh_token", sa.String(length=2048), nullable=True),
        sa.Column("account_email", sa.String(length=256), nullable=True),
        sa.Column("connected_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.pk"], ondelete="CASCADE"),
        sa.UniqueConstraint("tenant_id", "owner_user_id", "provider"),
    )
    op.create_index("ix_connector_accounts_tenant_id", "connector_accounts", ["tenant_id"])
    op.create_index("ix_connector_accounts_owner_user_id", "connector_accounts", ["owner_user_id"])


def downgrade() -> None:
    op.drop_index("ix_connector_accounts_owner_user_id", table_name="connector_accounts")
    op.drop_index("ix_connector_accounts_tenant_id", table_name="connector_accounts")
    op.drop_table("connector_accounts")
    op.drop_column("documents", "retain_original")
    op.drop_column("documents", "source_ref")
    op.drop_column("documents", "source")
