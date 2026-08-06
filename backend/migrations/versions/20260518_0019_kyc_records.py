"""kyc_records — durable per-extraction records

The Document.extracted_fields JSONB column shipped in 0018 is a single
snapshot per document. kyc_records lets us:
  - run extraction multiple times (model upgrade, re-OCR a blurry scan)
  - query across documents ("show all Aadhaar holders")
  - link extractions to a deduplicated subject via subject_pk (phase 3)

Strictly additive; existing extractions on documents.extracted_fields
stay readable.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB


revision: str = "0019_kyc_records"
down_revision: Union[str, Sequence[str], None] = "0018_document_extracted_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "kyc_records",
        sa.Column("pk", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(64), index=True, nullable=False),
        sa.Column("document_pk", sa.Integer, sa.ForeignKey("documents.pk", ondelete="CASCADE"), nullable=False),
        sa.Column("subject_pk", sa.Integer, nullable=True),
        sa.Column("doc_type", sa.String(64), nullable=False),
        sa.Column("fields", JSONB, nullable=False, server_default="{}"),
        sa.Column("confidence", sa.Float, nullable=True),
        sa.Column("model", sa.String(128), nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("extracted_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_kyc_records_doc", "kyc_records", ["document_pk"])
    op.create_index("ix_kyc_records_subject", "kyc_records", ["subject_pk"])


def downgrade() -> None:
    op.drop_index("ix_kyc_records_subject", table_name="kyc_records")
    op.drop_index("ix_kyc_records_doc", table_name="kyc_records")
    op.drop_table("kyc_records")
