"""kyc_subjects — deduplicated KYC personas

One row per identified individual / entity across all their documents.
Created/updated by the identity stitcher: when a new kyc_record lands
with (holder_name, dob), the stitcher finds an existing subject in the
same tenant matching on fuzzy-name + exact-DOB (or exact-document-number
for business KYB) and links the record; otherwise creates a new subject.

`status` lifecycle:
  pending   — at least one record but no clear identity match yet
  partial   — identity established (≥ 1 ID + DOB) but missing requirements
  verified  — all configured KYC requirements have linked records

The `requirement_coverage` JSONB stores a map of {requirement_id: doc_id}
so the Subjects view can render the per-subject coverage table without
re-joining.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB


revision: str = "0020_kyc_subjects"
down_revision: Union[str, Sequence[str], None] = "0019_kyc_records"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "kyc_subjects",
        sa.Column("pk", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(64), index=True, nullable=False),
        sa.Column("canonical_name", sa.String(256), nullable=False),
        sa.Column("canonical_dob", sa.String(16), nullable=True),
        sa.Column("subject_kind", sa.String(32), nullable=False, server_default="individual"),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("doc_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("requirement_coverage", JSONB, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_kyc_subjects_name_dob", "kyc_subjects", ["tenant_id", "canonical_dob"])

    # Wire kyc_records.subject_pk to kyc_subjects.pk
    op.create_foreign_key(
        "fk_kyc_records_subject",
        "kyc_records", "kyc_subjects",
        ["subject_pk"], ["pk"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_kyc_records_subject", "kyc_records", type_="foreignkey")
    op.drop_index("ix_kyc_subjects_name_dob", table_name="kyc_subjects")
    op.drop_table("kyc_subjects")
