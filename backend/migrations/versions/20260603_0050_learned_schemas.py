"""M46 · learned_schemas · self-learning extraction schema per doc-type cluster.

Tenant-scoped metadata the universal extractor accumulates: which field labels +
record kinds recur for each classifier doc_type. Used to hint future extractions
of the same kind. Additive — documents product only writes it; audit ignores it.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0050_learned_schemas"
down_revision: Union[str, Sequence[str], None] = "0049_drive_connector"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "learned_schemas",
        sa.Column("pk", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("doc_type", sa.String(length=128), nullable=False),
        sa.Column("fields", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.Column("record_kinds", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.Column("seen_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("tenant_id", "doc_type"),
    )
    op.create_index("ix_learned_schemas_tenant_id", "learned_schemas", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_learned_schemas_tenant_id", table_name="learned_schemas")
    op.drop_table("learned_schemas")
