"""M48 · email verification flag for self-registered Documents users.

New email/password signups start unverified and must confirm via a Resend email.
Existing users are grandfathered to verified=true so the launch doesn't lock
anyone out. Google sign-ins are marked verified at provision time (Google
already verified the address).
"""
from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op

revision: str = "0066_email_verified"
down_revision: Union[str, Sequence[str], None] = "0065_user_plan_trial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("email_verified", sa.Boolean(), nullable=False,
                                     server_default="false"))
    # Grandfather every existing account → verified (don't lock out current users).
    op.execute("UPDATE users SET email_verified = true")


def downgrade() -> None:
    op.drop_column("users", "email_verified")
