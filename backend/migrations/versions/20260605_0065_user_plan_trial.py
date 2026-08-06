"""M47 · per-user subscription plan + 7-day trial.

Each self-registered Documents user starts on a 7-day full trial, then falls back
to 'free' (capped) unless they're on 'pro'/'enterprise'. Superadmin can override
plan + trial end.
"""
from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op

revision: str = "0065_user_plan_trial"
down_revision: Union[str, Sequence[str], None] = "0064_connector_encrypt_files"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("plan", sa.String(length=16), nullable=False, server_default="trial"))
    op.add_column("users", sa.Column("trial_ends_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("plan_since", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "plan_since")
    op.drop_column("users", "trial_ends_at")
    op.drop_column("users", "plan")
