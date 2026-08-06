"""token_version on users — session revocation (bump to invalidate all live JWTs)

Revision ID: 0099_user_token_version
Revises: 0098_apiclient_owner
Create Date: 2026-07-13
"""
from alembic import op
import sqlalchemy as sa

revision = "0099_user_token_version"
down_revision = "0098_apiclient_owner"
branch_labels = None
depends_on = None


def upgrade():
    # Additive, NOT NULL default 0 — existing sessions carry no `tv` claim and are
    # unaffected until the flag is on AND the counter is bumped.
    op.add_column("users", sa.Column("token_version", sa.Integer(), nullable=False,
                                     server_default="0"))


def downgrade():
    op.drop_column("users", "token_version")
