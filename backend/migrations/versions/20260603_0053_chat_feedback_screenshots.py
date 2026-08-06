"""M46 · chat_feedback · screenshots.

Adds a JSONB `screenshots` column holding up to 3 client-side-compressed JPEG
data URLs attached to a 👎 report, so the issue can be SEEN, not just read.
Additive + nullable.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0053_chat_feedback_screenshots"
down_revision: Union[str, Sequence[str], None] = "0052_chat_feedback_rich"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("chat_feedback", sa.Column("screenshots", JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("chat_feedback", "screenshots")
