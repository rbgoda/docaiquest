"""M46 · §compliance · consent_records — GDPR/PDPA consent capture.

Records each user's explicit consent: 'processing' (at signup — processing +
third-party LLM sub-processors) and 'personal_data' (one-time acknowledgement,
before the first upload, that documents may contain personal / special-category
health data). Each row carries the consent VERSION + timestamp for auditability.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0060_consent_records"
down_revision: Union[str, Sequence[str], None] = "0059_learned_type_centroid"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "consent_records",
        sa.Column("pk", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("version", sa.String(length=32), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("tenant_id", "user_id", "kind", name="uq_consent_user_kind"),
    )
    op.create_index("ix_consent_records_user_id", "consent_records", ["user_id"])


def downgrade() -> None:
    op.drop_table("consent_records")
