"""M44.P11 · llm_call_audit · provable record of every external LLM call.

One row per gateway.call. Stores HASHES of prompt + response so we can
prove a call happened and verify it wasn't tampered with later, without
ourselves becoming a PII custodian.

Reads of this table answer compliance-grade questions:
  · "Did tenant EU-X's data ever leave EU jurisdiction?" → filter on
     data_residency
  · "Show me every LLM call made for document Y" → filter on doc_id_external
  · "How much PII was redacted from prompts last quarter?" → SUM(pii_entities_redacted)
  · "Which providers did we use for tenant Z?" → DISTINCT(provider)

Indexes are tuned for the common compliance-report query shapes.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0042_llm_call_audit"
down_revision: Union[str, Sequence[str], None] = "0041_document_artifacts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "llm_call_audit",
        sa.Column("pk", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=False, index=True),
        sa.Column("user_email", sa.String(length=256), nullable=True),
        # Provider / model identity
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("task_kind", sa.String(length=32), nullable=True),
        # What we were doing it for
        sa.Column("doc_id_external", sa.String(length=64), nullable=True, index=True),
        # Hashes · never the content itself
        sa.Column("prompt_sha256", sa.String(length=64), nullable=False),
        sa.Column("response_sha256", sa.String(length=64), nullable=True),
        # Token usage straight from provider
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        # PII redaction telemetry
        sa.Column("pii_entities_redacted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("pii_kinds", JSONB(), nullable=True),
        # Compliance signal · region the provider sits in
        sa.Column("data_residency", sa.String(length=16), nullable=True),
        # Call observability
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("http_status", sa.SmallInteger(), nullable=True),
        sa.Column("failure_kind", sa.String(length=32), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
    )
    op.create_index(
        "ix_llm_audit_tenant_time",
        "llm_call_audit",
        ["tenant_id", sa.text("created_at DESC")],
    )
    op.create_index(
        "ix_llm_audit_provider_time",
        "llm_call_audit",
        ["provider", sa.text("created_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_llm_audit_provider_time", table_name="llm_call_audit")
    op.drop_index("ix_llm_audit_tenant_time", table_name="llm_call_audit")
    op.drop_table("llm_call_audit")
