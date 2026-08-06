"""M46 · §2 · learned_doc_types centroid — cheap distilled classification.

Stores a running-mean embedding (centroid) per learned type + the count averaged
in. On ingest, a new doc's embedding is matched against these centroids; a close
match auto-assigns the type with NO LLM call (Phase 2 distillation).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

from app.config import get_settings

revision: str = "0059_learned_type_centroid"
down_revision: Union[str, Sequence[str], None] = "0058_group_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    dim = get_settings().embed_dim
    op.add_column("learned_doc_types", sa.Column("centroid", Vector(dim), nullable=True))
    op.add_column("learned_doc_types",
                  sa.Column("centroid_n", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    op.drop_column("learned_doc_types", "centroid_n")
    op.drop_column("learned_doc_types", "centroid")
