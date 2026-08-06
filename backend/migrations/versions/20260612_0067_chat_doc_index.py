"""M49 · index chat_messages by (tenant_id, doc_id_external).

The per-document chat thread + history queries filter on
(tenant_id, doc_id_external) but doc_id_external was unindexed — every chat GET
scanned by tenant. This composite index serves the hot doc-chat path.
"""
from typing import Sequence, Union
from alembic import op

revision: str = "0067_chat_doc_index"
down_revision: Union[str, Sequence[str], None] = "0066_email_verified"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_chat_messages_tenant_doc", "chat_messages",
                    ["tenant_id", "doc_id_external"])


def downgrade() -> None:
    op.drop_index("ix_chat_messages_tenant_doc", table_name="chat_messages")
