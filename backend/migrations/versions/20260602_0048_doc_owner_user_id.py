"""M46 · documents.owner_user_id · per-user workspace ownership for the
Documents System product.

In a documents stack (DOCAIQ_PRODUCT=documents) every self-registered user gets
a private workspace: their uploads carry their user pk in `owner_user_id`, and
the repository/retrieval layer scopes every read to the current user's pk so
users never see each other's documents or chats.

Additive + nullable → no backfill. NULL means "no per-user owner": that's the
permanent state in the auditing product (which never sets the per-user scope),
and it's also how any pre-existing documents in a freshly-converted documents
stack read (they'd be invisible to per-user-scoped reads — acceptable because
the production documents stack boots empty, DOCAIQ_SEED_DEMO_DATA=false).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# NOTE: revision id must be ≤ 32 chars (alembic_version.version_num is
# varchar(32)). The descriptive name lives in the filename, not the id.
revision: str = "0048_doc_owner_user_id"
down_revision: Union[str, Sequence[str], None] = "0047_marketplace_slug"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column("owner_user_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_documents_owner_user_id",
        "documents",
        "users",
        ["owner_user_id"],
        ["pk"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_documents_owner_user_id",
        "documents",
        ["tenant_id", "owner_user_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_documents_owner_user_id", table_name="documents")
    op.drop_constraint("fk_documents_owner_user_id", "documents", type_="foreignkey")
    op.drop_column("documents", "owner_user_id")
