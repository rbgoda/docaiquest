"""human review on product_feedback — accept-as-resolved vs follow-up-needed + note

Revision ID: 0100_feedback_followup
Revises: 0099_user_token_version
Create Date: 2026-07-13
"""
from alembic import op
import sqlalchemy as sa

revision = "0100_feedback_followup"
down_revision = "0099_user_token_version"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("product_feedback", sa.Column("followup_needed", sa.Boolean(),
                                                nullable=False, server_default="false"))
    op.add_column("product_feedback", sa.Column("followup_note", sa.Text(), nullable=True))


def downgrade():
    op.drop_column("product_feedback", "followup_note")
    op.drop_column("product_feedback", "followup_needed")
