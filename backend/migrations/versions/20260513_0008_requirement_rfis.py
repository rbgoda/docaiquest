"""requirement_rfis (M13)

Adds the requirement_rfis table — Request-for-Info threads raised by
reviewers on a specific (audit_run, requirement) pair, with vendor
response and resolve fields.

Revision ID: 0008_requirement_rfis
Revises: 0007_audit_run_verdicts
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0008_requirement_rfis"
down_revision: Union[str, Sequence[str], None] = "0007_audit_run_verdicts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "requirement_rfis",
        sa.Column("pk", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("audit_run_pk", sa.Integer(), nullable=False),
        sa.Column("requirement_pk", sa.Integer(), nullable=False),
        sa.Column("raised_by", sa.String(length=256), nullable=False),
        sa.Column("raised_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="open"),
        sa.Column("vendor_response", sa.Text(), nullable=True),
        sa.Column("responded_by", sa.String(length=256), nullable=True),
        sa.Column("responded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by", sa.String(length=256), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["audit_run_pk"], ["audit_runs.pk"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["requirement_pk"], ["requirements.pk"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("pk"),
    )
    op.create_index("ix_requirement_rfis_tenant_id", "requirement_rfis", ["tenant_id"])
    op.create_index("ix_requirement_rfis_audit_run_pk", "requirement_rfis", ["audit_run_pk"])
    op.create_index("ix_requirement_rfis_requirement_pk", "requirement_rfis", ["requirement_pk"])


def downgrade() -> None:
    op.drop_index("ix_requirement_rfis_requirement_pk", table_name="requirement_rfis")
    op.drop_index("ix_requirement_rfis_audit_run_pk", table_name="requirement_rfis")
    op.drop_index("ix_requirement_rfis_tenant_id", table_name="requirement_rfis")
    op.drop_table("requirement_rfis")
