"""learned_schemas.field_examples — example values/type stats for typed schemas

Populated ONLY for training-eligible (free-plan + model-training-consented) documents.
Feeds schema crystallization's type/enum inference. Paid documents stay field-names-only
(this column empty). Values are capped + curated; consent-gated.

Revision ID: 0084_learned_schema_examples
Revises: 0083_generated_schemas
Create Date: 2026-07-02
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0084_learned_schema_examples"
down_revision: Union[str, Sequence[str], None] = "0083_generated_schemas"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("learned_schemas",
                  sa.Column("field_examples", JSONB(), nullable=False, server_default="{}"))


def downgrade() -> None:
    op.drop_column("learned_schemas", "field_examples")
