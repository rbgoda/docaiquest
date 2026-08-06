"""owner_user_id on api_clients — enterprise self-serve keys are scoped to one user's documents

Revision ID: 0098_apiclient_owner
Revises: 0097_llm_document_pk
Create Date: 2026-07-07
"""
from alembic import op
import sqlalchemy as sa

revision = "0098_apiclient_owner"
down_revision = "0097_llm_document_pk"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("api_clients", sa.Column("owner_user_id", sa.Integer(), nullable=True))
    op.create_index("ix_api_clients_owner_user_id", "api_clients", ["owner_user_id"])


def downgrade():
    op.drop_index("ix_api_clients_owner_user_id", table_name="api_clients")
    op.drop_column("api_clients", "owner_user_id")
