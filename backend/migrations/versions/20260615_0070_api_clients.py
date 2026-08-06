"""v1 API/SDK · third-party API clients (per-partner keys).

Generalizes the single DOCAIQ_EXTRACTION_API_KEY into per-partner, scoped,
rate-limited keys. Only the SHA-256 hash is stored. Additive; no backfill —
the legacy env key keeps working via app/api_clients.require_client.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0070_api_clients"
down_revision: Union[str, Sequence[str], None] = "0069_document_tags"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "api_clients",
        sa.Column("pk", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("key_prefix", sa.String(24), nullable=False),
        sa.Column("key_hash", sa.String(64), nullable=False),
        sa.Column("env", sa.String(8), nullable=False, server_default="live"),
        sa.Column("scopes", JSONB, nullable=True),
        sa.Column("rate_limit_rpm", sa.Integer, nullable=False, server_default="120"),
        sa.Column("monthly_token_budget", sa.Integer, nullable=True),
        sa.Column("created_by", sa.String(256), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_api_clients_tenant_id", "api_clients", ["tenant_id"])
    op.create_index("ix_api_clients_key_hash", "api_clients", ["key_hash"], unique=True)
    op.create_index("ix_api_clients_key_prefix", "api_clients", ["key_prefix"])


def downgrade() -> None:
    op.drop_index("ix_api_clients_key_prefix", table_name="api_clients")
    op.drop_index("ix_api_clients_key_hash", table_name="api_clients")
    op.drop_index("ix_api_clients_tenant_id", table_name="api_clients")
    op.drop_table("api_clients")
