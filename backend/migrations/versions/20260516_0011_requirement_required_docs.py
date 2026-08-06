"""requirements.required_docs

Per-requirement list of acceptable evidence labels (the documents a reviewer
expects to see attached). Today the matcher / reviewer infers this from the
requirement title alone; making it a first-class field lets admins curate the
list during framework setup and lets the matcher score doc-vs-requirement
relevance more precisely.

JSONB array of strings, default `[]` so existing rows stay valid without a
backfill. Examples for a typical SOC 2 access-review requirement:
    ["Quarterly access review report",
     "Joiner-mover-leaver runbook",
     "HR termination notification log"]
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB


revision: str = "0011_requirement_required_docs"
down_revision: Union[str, Sequence[str], None] = "0010_vendor_primary_reviewer"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "requirements",
        sa.Column("required_docs", JSONB, nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    op.drop_column("requirements", "required_docs")
