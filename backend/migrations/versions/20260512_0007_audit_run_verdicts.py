"""audit_run_requirements join + drop verdict cols from requirements

Each (audit_run, requirement) pair now owns its own verdict so the same
requirement (e.g. REQ-027) can be approved for Atlas and rejected for
Helios in the same tenant.

Revision ID: 0007_audit_run_verdicts
Revises: 0006_llm_calls
Create Date: 2026-05-12 22:00:00

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007_audit_run_verdicts"
down_revision: Union[str, Sequence[str], None] = "0006_llm_calls"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Create the join table.
    op.create_table(
        "audit_run_requirements",
        sa.Column("pk", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("audit_run_pk", sa.Integer, sa.ForeignKey("audit_runs.pk", ondelete="CASCADE"), nullable=False),
        sa.Column("requirement_pk", sa.Integer, sa.ForeignKey("requirements.pk", ondelete="CASCADE"), nullable=False),
        sa.Column("verdict", sa.String(16), nullable=True),
        sa.Column("verdict_at", sa.String(64), nullable=True),
        sa.Column("verdict_by", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("audit_run_pk", "requirement_pk"),
    )
    op.create_index("ix_audit_run_requirements_tenant_id", "audit_run_requirements", ["tenant_id"])
    op.create_index("ix_audit_run_requirements_audit_run_pk", "audit_run_requirements", ["audit_run_pk"])
    op.create_index("ix_audit_run_requirements_requirement_pk", "audit_run_requirements", ["requirement_pk"])

    # 2. Pre-populate: one row per (audit_run, requirement) pair per tenant.
    #    Any existing verdict on the requirement gets copied to the FIRST
    #    audit run for that tenant (best-effort; in practice these were dev
    #    test verdicts and the mapping is approximate anyway).
    op.execute("""
        INSERT INTO audit_run_requirements (tenant_id, audit_run_pk, requirement_pk, verdict, verdict_at, verdict_by)
        SELECT
            ar.tenant_id,
            ar.pk          AS audit_run_pk,
            r.pk           AS requirement_pk,
            NULL AS verdict, NULL AS verdict_at, NULL AS verdict_by
        FROM audit_runs ar
        JOIN requirements r ON r.tenant_id = ar.tenant_id
    """)

    # 3. Migrate any pre-existing verdict on the requirement to the FIRST
    #    audit run row for that tenant (a one-time fix-up; new mutations go
    #    direct to the join).
    op.execute("""
        UPDATE audit_run_requirements arr
        SET verdict = r.verdict, verdict_at = r.verdict_at, verdict_by = r.verdict_by
        FROM requirements r,
             (SELECT tenant_id, MIN(pk) AS first_pk FROM audit_runs GROUP BY tenant_id) first_run
        WHERE arr.requirement_pk = r.pk
          AND arr.audit_run_pk = first_run.first_pk
          AND arr.tenant_id    = first_run.tenant_id
          AND r.verdict        IS NOT NULL
    """)

    # 4. Drop the verdict columns from requirements — the join is now the
    #    single source of truth.
    op.drop_column("requirements", "verdict_by")
    op.drop_column("requirements", "verdict_at")
    op.drop_column("requirements", "verdict")


def downgrade() -> None:
    op.add_column("requirements", sa.Column("verdict", sa.String(16), nullable=True))
    op.add_column("requirements", sa.Column("verdict_at", sa.String(64), nullable=True))
    op.add_column("requirements", sa.Column("verdict_by", sa.String(128), nullable=True))

    op.drop_index("ix_audit_run_requirements_requirement_pk", table_name="audit_run_requirements")
    op.drop_index("ix_audit_run_requirements_audit_run_pk", table_name="audit_run_requirements")
    op.drop_index("ix_audit_run_requirements_tenant_id", table_name="audit_run_requirements")
    op.drop_table("audit_run_requirements")
