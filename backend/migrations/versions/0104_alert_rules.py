"""Add alert_rules table for user-defined alert rules.

Revision ID: 0104_alert_rules
Revises: 0103_dashboard_configs
Create Date: 2026-07-26
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0104_alert_rules'
down_revision: Union[str, Sequence[str], None] = '0103_dashboard_configs'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'alert_rules',
        sa.Column('pk', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('tenant_id', sa.String(64), nullable=False, index=True),
        sa.Column('owner_user_id', sa.Integer(), nullable=False, index=True),
        sa.Column('name', sa.String(200), nullable=False),
        sa.Column('rule_type', sa.String(32), nullable=False),
        sa.Column('config', postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default='t'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('pk'),
    )


def downgrade() -> None:
    op.drop_table('alert_rules')
