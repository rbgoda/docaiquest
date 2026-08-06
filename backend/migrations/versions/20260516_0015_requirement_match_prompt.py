"""requirements.match_prompt

Optional per-requirement override for the matcher's user prompt. When set,
the matcher uses this verbatim instead of the generic
'Does this document satisfy the requirement "X"?' template. Lets admins
tune matcher behaviour surgically per control — useful for ambiguous
requirements where the generic prompt is too soft.

Example values:
    "Look for a signed certificate from a Big 4 firm dated within the last 12 months."
    "Must explicitly state multi-factor authentication AND specify a cadence."
    "Confirm the document references HIPAA §164.308(a)(1) by name."

NULL = use generic template (matches today's behaviour). Empty string is
normalised to NULL by the repo writer.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0015_requirement_match_prompt"
down_revision: Union[str, Sequence[str], None] = "0014_audit_run_closed"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "requirements",
        sa.Column("match_prompt", sa.Text, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("requirements", "match_prompt")
