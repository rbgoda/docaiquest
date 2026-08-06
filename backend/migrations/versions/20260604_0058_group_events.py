"""M46 · §1 · document_group_events — group activity log.

Records who did what in a group (created/renamed, member added/removed, doc
shared/unshared) so members can see a history. Additive; documents-product only.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0058_group_events"
down_revision: Union[str, Sequence[str], None] = "0057_reflexion_owner"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "document_group_events",
        sa.Column("pk", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("group_id", sa.Integer(),
                  sa.ForeignKey("document_groups.pk", ondelete="CASCADE"), nullable=False),
        sa.Column("actor_user_id", sa.Integer(), nullable=True),
        sa.Column("actor_email", sa.String(length=256), nullable=True),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("detail", sa.String(length=512), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_document_group_events_tenant_id", "document_group_events", ["tenant_id"])
    op.create_index("ix_document_group_events_group_id", "document_group_events", ["group_id"])


def downgrade() -> None:
    op.drop_table("document_group_events")
