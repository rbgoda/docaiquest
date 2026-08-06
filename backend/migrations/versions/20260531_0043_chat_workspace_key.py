"""M44.P12 · chat_messages.workspace_key · anchor cross-document chat threads.

Until now a `chat_messages` row was anchored by EITHER `requirement_id_external`
(Review chat) OR `doc_id_external` (chat-with-a-single-document). The new
"overall documents chat" (cross-doc Q&A over a vendor's whole document set)
needs a third anchor that points at neither a requirement nor one document.

`workspace_key` is that anchor. Values look like `vendor:<vendor_pk>` (chat
scoped to one vendor's documents) or `tenant` (all-tenant scope, future). When
set, both `requirement_id_external` and `doc_id_external` are NULL on the row.

Additive + nullable → zero behaviour change for existing rows and code paths.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0043_chat_workspace_key"
down_revision: Union[str, Sequence[str], None] = "0042_llm_call_audit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "chat_messages",
        sa.Column("workspace_key", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_chat_messages_tenant_workspace",
        "chat_messages",
        ["tenant_id", "workspace_key"],
    )


def downgrade() -> None:
    op.drop_index("ix_chat_messages_tenant_workspace", table_name="chat_messages")
    op.drop_column("chat_messages", "workspace_key")
