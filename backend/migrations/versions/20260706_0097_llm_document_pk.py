"""add document_pk to llm_calls for per-document cost attribution

Revision ID: 0097_llm_document_pk
Revises: 0096_superadmin_allow
Create Date: 2026-07-06
"""
from alembic import op
import sqlalchemy as sa

revision = "0097_llm_document_pk"
down_revision = "0096_superadmin_allow"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("llm_calls", sa.Column("document_pk", sa.Integer(), nullable=True))
    op.create_index("ix_llm_calls_document_pk", "llm_calls", ["document_pk"])


def downgrade():
    op.drop_index("ix_llm_calls_document_pk", table_name="llm_calls")
    op.drop_column("llm_calls", "document_pk")
