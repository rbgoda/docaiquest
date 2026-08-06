"""M31.6 · multi-evidence per audit_run_requirements.

Requirement.doc_id_external is a single FK — so a requirement like
'Date of birth on file' could only be backed by ONE document, even when
BOTH a passport AND an Aadhaar card carry the holder's DOB. The
matcher attached whichever doc came first; the other was lost.

This adds evidence_docs (JSONB list) per (audit_run, requirement) so
multiple docs can all back the same control. The legacy single doc
stays as 'primary' (highest confidence) for the UI's one-column layout.

Backfill: copy existing Requirement.doc_id_external into evidence_docs
for each audit_run_requirements row so the new list shows what's
already attached.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0035_evidence_docs"
down_revision: Union[str, Sequence[str], None] = "0034_extracted_fields_gin"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "audit_run_requirements",
        sa.Column("evidence_docs", JSONB(), nullable=True),
    )
    # Backfill from existing single attachment so the new list isn't empty
    # for already-matched audits.
    op.execute(
        """
        UPDATE audit_run_requirements AS arr
        SET evidence_docs = jsonb_build_array(jsonb_build_object(
            'doc_id', r.doc_id_external,
            'confidence', r.confidence,
            'attached_at', NULL,
            'attached_by', 'ai',
            'source', 'legacy_primary'
        ))
        FROM requirements r
        WHERE arr.requirement_pk = r.pk
          AND arr.tenant_id = r.tenant_id
          AND r.doc_id_external IS NOT NULL
          AND (arr.evidence_docs IS NULL OR arr.evidence_docs = '[]'::jsonb)
        """
    )


def downgrade() -> None:
    op.drop_column("audit_run_requirements", "evidence_docs")
