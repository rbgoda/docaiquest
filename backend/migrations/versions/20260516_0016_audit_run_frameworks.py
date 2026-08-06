"""audit_runs.frameworks (multi)

Adds a JSONB list of framework names per audit run. Lets a single audit
cover multiple frameworks at once — e.g. Atlas × (SOC 2 + HIPAA) — which
matches how real audit firms scope combined audits. The legacy
`framework` column stays for back-compat display (it now holds the first
framework or the joined list when multiple).

Backfill: existing rows get `frameworks = [framework]` so the matcher /
counters work uniformly across old and new audits without a code branch.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB


revision: str = "0016_audit_run_frameworks"
down_revision: Union[str, Sequence[str], None] = "0015_requirement_match_prompt"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "audit_runs",
        sa.Column("frameworks", JSONB, nullable=False, server_default="[]"),
    )
    # Backfill: jsonb_build_array casts the existing text framework into
    # a 1-element array. Idempotent — if a row already has a populated
    # frameworks array from a prior run, the WHERE preserves it.
    op.execute("""
        UPDATE audit_runs
           SET frameworks = jsonb_build_array(framework)
         WHERE jsonb_array_length(frameworks) = 0
           AND framework IS NOT NULL
           AND framework <> ''
    """)


def downgrade() -> None:
    op.drop_column("audit_runs", "frameworks")
