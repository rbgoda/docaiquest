"""M31.2.1 · audit_subjects.aliases

Add alternative names so a single subject can match documents that
spell their name differently (passport surname format, married name,
local-language spelling, OCR variant, etc).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0033_subject_aliases"
down_revision: Union[str, Sequence[str], None] = "0032_audit_subjects"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "audit_subjects",
        sa.Column("aliases", JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("audit_subjects", "aliases")
