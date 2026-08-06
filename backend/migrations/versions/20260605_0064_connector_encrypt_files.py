"""M46 · B7 · connector_accounts.encrypt_files — per-user 'encrypt my Drive files'.

Each user chooses whether files DocAIQ stores in their Drive are encrypted in
place (openable only via DocAIQ) or left plaintext (openable directly in Drive).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0064_connector_encrypt_files"
down_revision: Union[str, Sequence[str], None] = "0063_workspace_sync"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("connector_accounts",
                  sa.Column("encrypt_files", sa.Boolean(), nullable=False, server_default="false"))


def downgrade() -> None:
    op.drop_column("connector_accounts", "encrypt_files")
