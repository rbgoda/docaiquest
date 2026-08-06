"""M44.F1 · framework_packs.marketplace_slug · link an installed pack to its
marketplace source so versioning + install-delta can find it.

A marketplace install creates a local FrameworkPack whose `id_external` is
slugified from the framework NAME (e.g. "ISO/IEC 27001:2022" → iso-iec-27001-2022),
which doesn't match the marketplace slug (iso27001-2022). Without a stored link
we can't tell which local pack a marketplace slug maps to — so versioning checks
and re-install dedup both fail. `marketplace_slug` records that link.

Additive + nullable → no backfill; packs installed before this just won't show
upstream-update prompts until re-installed.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# NOTE: revision id must be ≤ 32 chars (alembic_version.version_num is
# varchar(32)). The descriptive name lives in the filename, not the id.
revision: str = "0047_marketplace_slug"
down_revision: Union[str, Sequence[str], None] = "0046_understanding_tier"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "framework_packs",
        sa.Column("marketplace_slug", sa.String(length=128), nullable=True),
    )
    op.create_index(
        "ix_framework_packs_marketplace_slug",
        "framework_packs",
        ["tenant_id", "marketplace_slug"],
    )


def downgrade() -> None:
    op.drop_index("ix_framework_packs_marketplace_slug", table_name="framework_packs")
    op.drop_column("framework_packs", "marketplace_slug")
