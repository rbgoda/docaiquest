"""field_edits · HITL audit trail for manual overrides on extracted_fields

Every time a reviewer manually edits a field in the Key Facts panel, one
row lands here recording the before/after, who, when, and why. The
extracted_fields JSONB on the document carries the LIVE value (whatever's
shown in the UI); this table carries the HISTORY so audit firms can prove
the chain of edits during sign-off.

field_path uses dotted notation matching the JSONB structure:
  fields.total                       — a scalar field
  fields.vendor.name                 — a nested scalar
  fields.top_transactions.0.category — the category of the first txn
  fields.line_items.3.amount         — the amount on the 4th line item

original_value + new_value are stored as TEXT (not JSONB) because reviewers
typically edit strings + numbers; complex object/array edits are rare and
just get JSON-stringified.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0025_field_edits"
down_revision: Union[str, Sequence[str], None] = "0024_merchant_category_cache"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "field_edits",
        sa.Column("pk", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(64), nullable=False, index=True),
        sa.Column(
            "document_pk",
            sa.Integer,
            sa.ForeignKey("documents.pk", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("field_path", sa.String(256), nullable=False),
        sa.Column("original_value", sa.Text, nullable=True),
        sa.Column("new_value", sa.Text, nullable=True),
        sa.Column("edited_by", sa.String(256), nullable=False),
        sa.Column("reason", sa.Text, nullable=True),
        sa.Column(
            "edited_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_field_edits_doc_path",
        "field_edits",
        ["document_pk", "field_path"],
    )


def downgrade() -> None:
    op.drop_index("ix_field_edits_doc_path", table_name="field_edits")
    op.drop_table("field_edits")
