"""M46 · §5 · workspace_sync — track each user's encrypted workspace in their Drive.

One row per user: the Drive file id of their workspace.sqlite, doc count, size,
last sync time. Additive; the workspace export is opt-in.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0063_workspace_sync"
down_revision: Union[str, Sequence[str], None] = "0062_share_drive_copy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workspace_sync",
        sa.Column("pk", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("owner_user_id", sa.Integer(), nullable=False),
        sa.Column("drive_file_id", sa.String(length=256), nullable=True),
        sa.Column("doc_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("tenant_id", "owner_user_id", name="uq_workspace_sync_owner"),
    )
    op.create_index("ix_workspace_sync_owner_user_id", "workspace_sync", ["owner_user_id"])


def downgrade() -> None:
    op.drop_table("workspace_sync")
