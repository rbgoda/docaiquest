"""v1 API · grant an API key access to specific groups (shared folders).

Adds `api_clients.allowed_group_ids` (JSONB list of group ids). A partner key
(e.g. AuditAIQ) is granted a customer's group so it can match audit requirements
against exactly that shared folder. Additive + nullable.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0071_api_client_groups"
down_revision: Union[str, Sequence[str], None] = "0070_api_clients"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("api_clients", sa.Column("allowed_group_ids", JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column("api_clients", "allowed_group_ids")
