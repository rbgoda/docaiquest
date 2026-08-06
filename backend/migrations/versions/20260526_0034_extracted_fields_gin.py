"""T2.1 · GIN index on documents.extracted_fields for structured queries.

Without this index, any matcher query that wants to filter by an
extracted field (e.g. 'find docs where extracted_fields.holder_name
matches X') falls back to a full table scan of the JSONB column. With
the index, jsonb_path_ops gives us fast `@>` containment matching.

Use case: matcher can answer 'is there a doc with extracted_fields ->
holder_name = Y' in a single index lookup instead of N chunk-similarity
calls.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0034_extracted_fields_gin"
down_revision: Union[str, Sequence[str], None] = "0033_subject_aliases"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # jsonb_path_ops · smaller index, supports @> and @? operators which
    # is what the matcher will use ("does the doc have THIS field=value?").
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_documents_extracted_fields_gin "
        "ON documents USING gin (extracted_fields jsonb_path_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_documents_extracted_fields_gin")
