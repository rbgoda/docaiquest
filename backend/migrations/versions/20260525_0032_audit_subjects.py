"""M31.2 · audit_subjects table.

KYC-style audits are subject-bound — they're "verify identity of Rajesh
Goda + John Doe", not just "verify Acme Inc". This table holds the named
subjects per audit so the matcher can reject documents that don't pertain
to any of them, and the UI can show per-subject context.

Subjects are managed via /api/audit-runs/{id}/subjects.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0032_audit_subjects"
down_revision: Union[str, Sequence[str], None] = "0031_audit_history_archive"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "audit_subjects",
        sa.Column("pk", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column(
            "audit_run_pk", sa.Integer(),
            sa.ForeignKey("audit_runs.pk", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("role", sa.String(length=64), nullable=True),
        sa.Column("dob", sa.String(length=32), nullable=True),
        sa.Column("nationality", sa.String(length=64), nullable=True),
        sa.Column("id_number", sa.String(length=128), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
    )
    op.create_index("ix_audit_subjects_tenant", "audit_subjects", ["tenant_id"])
    op.create_index("ix_audit_subjects_audit", "audit_subjects", ["audit_run_pk"])


def downgrade() -> None:
    op.drop_index("ix_audit_subjects_audit", table_name="audit_subjects")
    op.drop_index("ix_audit_subjects_tenant", table_name="audit_subjects")
    op.drop_table("audit_subjects")
