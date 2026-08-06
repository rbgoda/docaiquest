"""initial — all tables, every one tenant-scoped

Revision ID: 0001_initial
Revises:
Create Date: 2026-05-12 00:00:00

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers
revision: str = "0001_initial"
down_revision: Union[str, Sequence[str], None] = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- tenants ---------------------------------------------------------
    op.create_table(
        "tenants",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # --- audit_runs ------------------------------------------------------
    op.create_table(
        "audit_runs",
        sa.Column("pk", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("id_external", sa.String(64), nullable=False),
        sa.Column("vendor", sa.String(256), nullable=False),
        sa.Column("framework", sa.String(128), nullable=False),
        sa.Column("progress", sa.Integer, nullable=False),
        sa.Column("compliant", sa.Integer, nullable=False),
        sa.Column("review", sa.Integer, nullable=False),
        sa.Column("missing", sa.Integer, nullable=False),
        sa.Column("pending", sa.Integer, nullable=False),
        sa.Column("total", sa.Integer, nullable=False),
        sa.Column("due", sa.String(64), nullable=False),
        sa.Column("risk", sa.String(16), nullable=False),
        sa.Column("spend", sa.Float, nullable=False),
        sa.Column("started", sa.String(64), nullable=False),
        sa.Column("lead_reviewer", sa.String(128), nullable=False),
        sa.Column("vendor_contact", sa.String(128), nullable=False),
        sa.UniqueConstraint("tenant_id", "id_external"),
    )
    op.create_index("ix_audit_runs_tenant_id", "audit_runs", ["tenant_id"])

    # --- audit_history ---------------------------------------------------
    op.create_table(
        "audit_history",
        sa.Column("pk", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("id_external", sa.String(64), nullable=False),
        sa.Column("vendor", sa.String(256), nullable=False),
        sa.Column("framework", sa.String(128), nullable=False),
        sa.Column("closed", sa.String(64), nullable=False),
        sa.Column("verdict", sa.String(64), nullable=False),
        sa.Column("score", sa.Integer, nullable=False),
        sa.Column("duration", sa.String(64), nullable=False),
        sa.Column("findings", sa.Integer, nullable=False),
        sa.Column("critical_findings", sa.Integer, nullable=False),
        sa.Column("reviewer", sa.String(128), nullable=False),
        sa.Column("cost", sa.String(32), nullable=False),
        sa.UniqueConstraint("tenant_id", "id_external"),
    )
    op.create_index("ix_audit_history_tenant_id", "audit_history", ["tenant_id"])

    # --- documents -------------------------------------------------------
    op.create_table(
        "documents",
        sa.Column("pk", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("id_external", sa.String(64), nullable=False),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("path", sa.String(512), nullable=False),
        sa.Column("size", sa.String(32), nullable=False),
        sa.Column("modified", sa.String(64), nullable=False),
        sa.Column("pages", sa.Integer, nullable=False),
        sa.Column("current_page", sa.Integer, nullable=False),
        sa.Column("type", sa.String(64), nullable=False),
        sa.Column("content", sa.String(64), nullable=False),
        sa.UniqueConstraint("tenant_id", "id_external"),
    )
    op.create_index("ix_documents_tenant_id", "documents", ["tenant_id"])

    # --- requirements ----------------------------------------------------
    op.create_table(
        "requirements",
        sa.Column("pk", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("id_external", sa.String(64), nullable=False),
        sa.Column("group", sa.String(256), nullable=False),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("subtitle", sa.String(512), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("confidence", sa.Float, nullable=True),
        sa.Column("doc_id_external", sa.String(64), nullable=True),
        sa.Column("prior_doc_id_external", sa.String(64), nullable=True),
        sa.Column("verdict", sa.String(16), nullable=True),
        sa.Column("verdict_at", sa.String(64), nullable=True),
        sa.Column("verdict_by", sa.String(128), nullable=True),
        sa.UniqueConstraint("tenant_id", "id_external"),
    )
    op.create_index("ix_requirements_tenant_id", "requirements", ["tenant_id"])

    # --- vendors ---------------------------------------------------------
    op.create_table(
        "vendors",
        sa.Column("pk", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("id_external", sa.String(64), nullable=False),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("logo", sa.String(8), nullable=False),
        sa.Column("active_audits", sa.Integer, nullable=False),
        sa.Column("open_items", sa.Integer, nullable=False),
        sa.Column("score", sa.Integer, nullable=False),
        sa.Column("last_activity", sa.String(64), nullable=False),
        sa.Column("contacts", sa.Integer, nullable=False),
        sa.Column("frameworks", JSONB, nullable=False),
        sa.Column("tier", sa.String(64), nullable=False),
        sa.UniqueConstraint("tenant_id", "id_external"),
    )
    op.create_index("ix_vendors_tenant_id", "vendors", ["tenant_id"])

    # --- highlights ------------------------------------------------------
    op.create_table(
        "highlights",
        sa.Column("pk", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("id_external", sa.String(64), nullable=False),
        sa.Column("doc_id_external", sa.String(64), nullable=False),
        sa.Column("pin", sa.Integer, nullable=False),
        sa.Column("ref_label", sa.String(64), nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("page", sa.Integer, nullable=False),
        sa.Column("color", sa.String(16), nullable=False),
        sa.Column("is_box", sa.Boolean, nullable=True),
        sa.UniqueConstraint("tenant_id", "id_external"),
    )
    op.create_index("ix_highlights_tenant_id", "highlights", ["tenant_id"])
    op.create_index("ix_highlights_doc_id_external", "highlights", ["doc_id_external"])

    # --- chat_messages ---------------------------------------------------
    op.create_table(
        "chat_messages",
        sa.Column("pk", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("requirement_id_external", sa.String(64), nullable=False),
        sa.Column("role", sa.String(8), nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("bullets", JSONB, nullable=True),
        sa.Column("confidence", sa.Float, nullable=True),
        sa.Column("trace", JSONB, nullable=True),
        sa.Column("tools", JSONB, nullable=True),
        sa.Column("meta", sa.String(512), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_chat_messages_tenant_id", "chat_messages", ["tenant_id"])
    op.create_index("ix_chat_messages_requirement_id_external", "chat_messages", ["requirement_id_external"])

    # --- diffs -----------------------------------------------------------
    op.create_table(
        "diffs",
        sa.Column("pk", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("current_doc_id_external", sa.String(64), nullable=False),
        sa.Column("prior_doc_id_external", sa.String(64), nullable=False),
        sa.Column("sections", JSONB, nullable=False),
        sa.Column("summary", JSONB, nullable=False),
        sa.UniqueConstraint("tenant_id", "current_doc_id_external"),
    )
    op.create_index("ix_diffs_tenant_id", "diffs", ["tenant_id"])

    # --- routing_configs -------------------------------------------------
    op.create_table(
        "routing_configs",
        sa.Column("pk", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("config", JSONB, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False),
        sa.UniqueConstraint("tenant_id"),
    )


def downgrade() -> None:
    op.drop_table("routing_configs")
    op.drop_index("ix_diffs_tenant_id", table_name="diffs")
    op.drop_table("diffs")
    op.drop_index("ix_chat_messages_requirement_id_external", table_name="chat_messages")
    op.drop_index("ix_chat_messages_tenant_id", table_name="chat_messages")
    op.drop_table("chat_messages")
    op.drop_index("ix_highlights_doc_id_external", table_name="highlights")
    op.drop_index("ix_highlights_tenant_id", table_name="highlights")
    op.drop_table("highlights")
    op.drop_index("ix_vendors_tenant_id", table_name="vendors")
    op.drop_table("vendors")
    op.drop_index("ix_requirements_tenant_id", table_name="requirements")
    op.drop_table("requirements")
    op.drop_index("ix_documents_tenant_id", table_name="documents")
    op.drop_table("documents")
    op.drop_index("ix_audit_history_tenant_id", table_name="audit_history")
    op.drop_table("audit_history")
    op.drop_index("ix_audit_runs_tenant_id", table_name="audit_runs")
    op.drop_table("audit_runs")
    op.drop_table("tenants")
