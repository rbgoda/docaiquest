"""add_dashboard_configs_table

Revision ID: 0103_dashboard_configs
Revises: 349e522f23eb
Create Date: 2026-07-25 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = '0103_dashboard_configs'
down_revision: Union[str, Sequence[str], None] = '349e522f23eb'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'dashboard_configs',
        sa.Column('pk', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('tenant_id', sa.String(64), nullable=False),
        sa.Column('owner_user_id', sa.Integer(), nullable=False),
        sa.Column('config', JSONB(), nullable=False, server_default="[]"),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('pk'),
        sa.UniqueConstraint('tenant_id', 'owner_user_id',
                            name='uq_dashboard_configs_owner'),
    )
    op.create_index('ix_dashboard_configs_tenant_id', 'dashboard_configs', ['tenant_id'])
    op.create_index('ix_dashboard_configs_owner_user_id', 'dashboard_configs', ['owner_user_id'])


def downgrade() -> None:
    op.drop_table('dashboard_configs')
