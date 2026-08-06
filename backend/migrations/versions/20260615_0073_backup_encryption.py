"""Optional Drive-backup password encryption — per-user salt + check token.

Stores only the scrypt salt + a verification token (a sentinel encrypted with
the derived key). The password and the key are NEVER stored. Default: off
(backups remain unencrypted in the user's own Drive).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0073_backup_encryption"
down_revision: Union[str, Sequence[str], None] = "0072_saved_views"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("backup_encryption", sa.Boolean(), nullable=False,
                                     server_default="false"))
    op.add_column("users", sa.Column("backup_salt", sa.String(length=64), nullable=True))
    op.add_column("users", sa.Column("backup_check", sa.String(length=256), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "backup_check")
    op.drop_column("users", "backup_salt")
    op.drop_column("users", "backup_encryption")
