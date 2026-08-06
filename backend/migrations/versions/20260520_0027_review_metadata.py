"""M28 · document_reviews.metadata_json for learning-loop outcome capture.

Adds a JSONB column to each review-flip row so we can store the decision
context the auto-approve policy needs to learn from later:

  {
    "extraction_confidence": 0.92,
    "threshold_at_time": 0.95,
    "was_above_threshold": false,
    "reasons_at_review": [{"code": "...", "severity": "warn"}, ...],
    "hitl_edit_count": 2
  }

Used by /api/learning/document-threshold-suggestion to compute false-
positive / false-negative rates and propose a calibrated threshold.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0027_review_metadata"
down_revision: Union[str, Sequence[str], None] = "0026_review_signoff"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "document_reviews",
        sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("document_reviews", "metadata_json")
