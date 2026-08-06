"""M46 · A2 · document_group_shares.drive_copy_file_id — track the group's Drive copy.

share-to-group copies the file into the group's shared Drive folder; storing the
resulting Drive file id lets unshare delete that copy (not just the DB row).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0062_share_drive_copy"
down_revision: Union[str, Sequence[str], None] = "0061_documents_created_at"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("document_group_shares",
                  sa.Column("drive_copy_file_id", sa.String(length=256), nullable=True))


def downgrade() -> None:
    op.drop_column("document_group_shares", "drive_copy_file_id")
