"""golden_eval_cases — labeled extraction samples from consented free-tier docs

A golden evaluation corpus: one row per consented free document's extraction snapshot
(doc_type + fields + confidence + trust), so we can build/curate a real, diverse eval set
and track coverage by doc type. `verified` flips true when a human corrects/confirms the
fields (ground truth). Consent-gated (KIND_MODEL_TRAINING) — paid docs are never captured.
Superadmin-export only.

Revision ID: 0085_golden_eval_cases
Revises: 0084_learned_schema_examples
Create Date: 2026-07-02
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0085_golden_eval_cases"
down_revision: Union[str, Sequence[str], None] = "0084_learned_schema_examples"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "golden_eval_cases",
        sa.Column("pk", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=False, index=True),
        sa.Column("document_pk", sa.Integer(),
                  sa.ForeignKey("documents.pk", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("doc_id_external", sa.String(length=128), nullable=True),
        sa.Column("doc_type", sa.String(length=128), nullable=True),
        sa.Column("detected_doc_type", sa.String(length=128), nullable=True),
        sa.Column("fields", JSONB(), nullable=False, server_default="{}"),
        sa.Column("field_confidence", JSONB(), nullable=False, server_default="{}"),
        sa.Column("trust_score", sa.Float(), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=False, server_default="free_consented"),
        sa.Column("verified", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("edit_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("tenant_id", "document_pk", name="uq_golden_eval_tenant_doc"),
    )


def downgrade() -> None:
    op.drop_table("golden_eval_cases")
