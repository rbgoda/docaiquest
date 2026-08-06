"""documents.doc_type + alternatives — classify-first pipeline

Adds three columns to support M11.6 (classify-first / targeted matcher):
  - doc_type             · top-1 classifier output (passport, invoice, policy_doc, etc.)
  - doc_type_confidence  · float 0.0-1.0 for the top-1 choice
  - doc_type_alternatives · jsonb list of {type, confidence, evidence} for top-3

Indexed on doc_type so the router can filter candidates by type quickly
(e.g. 'find all docs classified as pen_test_report in this tenant').
All nullable for back-compat with previously-uploaded docs.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB


revision: str = "0021_document_classification"
down_revision: Union[str, Sequence[str], None] = "0020_kyc_subjects"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("doc_type", sa.String(64), nullable=True))
    op.add_column("documents", sa.Column("doc_type_confidence", sa.Float, nullable=True))
    op.add_column("documents", sa.Column("doc_type_alternatives", JSONB, nullable=True))
    op.create_index("ix_documents_doc_type", "documents", ["tenant_id", "doc_type"])


def downgrade() -> None:
    op.drop_index("ix_documents_doc_type", table_name="documents")
    op.drop_column("documents", "doc_type_alternatives")
    op.drop_column("documents", "doc_type_confidence")
    op.drop_column("documents", "doc_type")
