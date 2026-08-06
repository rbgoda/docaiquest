"""retrieval_metrics

Revision ID: 0105_retrieval_metrics
Revises: 0104_alert_rules
Create Date: 2026-07-28 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0105_retrieval_metrics'
down_revision: Union[str, Sequence[str], None] = '0104_alert_rules'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'retrieval_metrics',
        sa.Column('pk', sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column('tenant_id', sa.String(64), nullable=False, index=True),
        sa.Column('qhash', sa.String(32), nullable=False, index=True),
        sa.Column('hits_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('top_score', sa.REAL(), nullable=False, server_default='0'),
        sa.Column('bm25_candidates', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('cosine_candidates', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('latency_ms', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now(), index=True),
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_table('retrieval_metrics', if_exists=True)
