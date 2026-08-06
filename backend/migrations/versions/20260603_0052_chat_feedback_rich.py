"""M46 · chat_feedback · rich feedback modal (xpenseaiq-style box).

Adds category / suggestion / rating to chat_feedback so the 👎 form can capture
a structured signal (what kind of problem, what the fix is, an optional star
rating) instead of one free-text blob. Additive + nullable — old rows stay valid.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0052_chat_feedback_rich"
down_revision: Union[str, Sequence[str], None] = "0051_chat_feedback"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("chat_feedback", sa.Column("category", sa.String(length=16), nullable=True))
    op.add_column("chat_feedback", sa.Column("suggestion", sa.Text(), nullable=True))
    op.add_column("chat_feedback", sa.Column("rating", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("chat_feedback", "rating")
    op.drop_column("chat_feedback", "suggestion")
    op.drop_column("chat_feedback", "category")
