"""add_deprecated_at_to_entities_and_relations

Revision ID: 349e522f23eb
Revises: 0102_entity_identity
Create Date: 2026-07-25 03:02:04.081114

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '349e522f23eb'
down_revision: Union[str, Sequence[str], None] = '0102_entity_identity'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('entities', sa.Column('deprecated_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('entities', sa.Column('deprecated_by_run_pk', sa.Integer(), nullable=True))
    op.create_foreign_key(
        'fk_entities_deprecated_by_run_pk',
        'entities', 'graph_runs',
        ['deprecated_by_run_pk'], ['pk'],
        ondelete='SET NULL',
    )

    op.add_column('entity_relations', sa.Column('deprecated_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('entity_relations', sa.Column('deprecated_by_run_pk', sa.Integer(), nullable=True))
    op.create_foreign_key(
        'fk_entity_relations_deprecated_by_run_pk',
        'entity_relations', 'graph_runs',
        ['deprecated_by_run_pk'], ['pk'],
        ondelete='SET NULL',
    )


def downgrade() -> None:
    op.drop_constraint('fk_entities_deprecated_by_run_pk', 'entities', type_='foreignkey')
    op.drop_column('entities', 'deprecated_by_run_pk')
    op.drop_column('entities', 'deprecated_at')

    op.drop_constraint('fk_entity_relations_deprecated_by_run_pk', 'entity_relations', type_='foreignkey')
    op.drop_column('entity_relations', 'deprecated_by_run_pk')
    op.drop_column('entity_relations', 'deprecated_at')
