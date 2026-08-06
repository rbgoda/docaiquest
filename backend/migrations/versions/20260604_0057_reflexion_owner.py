"""M46 · §4 · reflexion_pairs.owner_user_id — close the cross-user cache leak.

The reflexion cache (and the few-shot 'common mistakes' preamble) was scoped by
tenant only. In the documents product every user shares one tenant, so a cached
answer / critique from user A could surface for user B. Add a per-owner column;
serving reads filter on it. Additive + nullable: existing rows (owner NULL) are
simply never served to a documents user (the read filter requires an exact owner
match when an owner is in context). Auditing has no owner in context → unchanged.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0057_reflexion_owner"
down_revision: Union[str, Sequence[str], None] = "0056_document_group_shares"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("reflexion_pairs",
                  sa.Column("owner_user_id", sa.Integer(), nullable=True))
    op.create_index("ix_reflexion_pairs_owner_user_id", "reflexion_pairs", ["owner_user_id"])


def downgrade() -> None:
    op.drop_index("ix_reflexion_pairs_owner_user_id", table_name="reflexion_pairs")
    op.drop_column("reflexion_pairs", "owner_user_id")
