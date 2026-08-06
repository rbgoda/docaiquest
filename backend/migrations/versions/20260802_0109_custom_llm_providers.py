"""custom_llm_providers

Revision ID: 0109_custom_llm_providers
Revises: 0108_translations
Create Date: 2026-08-02 00:00:00.000000

Custom (OpenAI-compatible) LLM provider registry — superadmin-defined providers
(Groq, Together AI, Fireworks, vLLM, etc.) stored per-tenant with encrypted API keys.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0109_custom_llm_providers"
down_revision: Union[str, None] = "0108_translations"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "custom_llm_providers",
        sa.Column("tenant_id", sa.String(64), primary_key=True),
        sa.Column("slug", sa.String(64), primary_key=True),
        sa.Column("label", sa.String(120), nullable=False),
        sa.Column("base_url", sa.String(255), nullable=False),
        sa.Column("api_key_enc", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("default_model", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("custom_llm_providers")
