"""documents.extracted_fields

Adds a JSONB column to persist KYC field extraction output per document.
Populated by the KYC extractor agent after the matcher auto-attaches a
doc to a KYC-* requirement. NULL for non-KYC docs.

Additive only; existing rows get NULL via the column default.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB


revision: str = "0018_document_extracted_fields"
down_revision: Union[str, Sequence[str], None] = "0017_vendor_archived"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column("extracted_fields", JSONB, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("documents", "extracted_fields")
