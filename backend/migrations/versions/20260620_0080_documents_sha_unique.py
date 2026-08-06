"""partial unique index on documents (tenant_id, owner_user_id, sha256)

Prevents a concurrent double-upload (user double-clicks, or upload + autosync race)
from creating two rows for the same file in one user's workspace. Partial so it
only constrains real connector/upload docs:
  · WHERE sha256 IS NOT NULL    — seeded/demo docs have no sha
  · WHERE owner_user_id IS NOT NULL — auditing-product docs aren't per-user scoped

Dupe-tolerant: if an environment already has duplicate (tenant, owner, sha) rows,
creating a UNIQUE index would fail and break the deploy — so we catch that and
fall back to a NON-unique index (no data loss, still helps the lookup). Prod has
0 documents at write time, so it gets the unique index cleanly.

Revision ID: 0080_documents_sha_unique
Revises: 0079_app_instances
Create Date: 2026-06-20
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0080_documents_sha_unique"
down_revision: Union[str, Sequence[str], None] = "0079_app_instances"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_IDX = "uq_documents_owner_sha"


def upgrade() -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
            BEGIN
                CREATE UNIQUE INDEX {_IDX} ON documents
                    (tenant_id, owner_user_id, sha256)
                    WHERE sha256 IS NOT NULL AND owner_user_id IS NOT NULL;
            EXCEPTION WHEN unique_violation THEN
                -- pre-existing duplicates → keep all data, create a non-unique
                -- index instead so the deploy never fails.
                CREATE INDEX IF NOT EXISTS {_IDX} ON documents
                    (tenant_id, owner_user_id, sha256)
                    WHERE sha256 IS NOT NULL AND owner_user_id IS NOT NULL;
                RAISE NOTICE 'duplicate (tenant,owner,sha256) rows exist — created NON-unique %', '{_IDX}';
            END;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {_IDX}")
