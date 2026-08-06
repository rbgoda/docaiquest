"""clean schema_library rows: drop leaked JSON-Schema definition-metadata keys

Some Schema-Architect-drafted schemas flattened a field-definition's own metadata
('required' / 'description' / 'type' / ...) into their top-level field map, so those
keys rendered as permanent phantom 'missing' rows in the JSON Schema view. The source
is fixed in #287 (schema_architect._sanitize_fields) and the render layer strips them
in #286 (schema_json._clean_fields). This data migration rewrites the already-stored
rows in-place so every environment's data is clean and the render-time guard can
eventually be retired.

Minimal + targeted: it ONLY drops the leaked metadata keys (a reserved-metadata name
carrying a NON-dict value); it never rewrites genuine field definitions.

Revision ID: 0101_schema_lib_drop_leaked_meta
Revises: 0100_feedback_followup
Create Date: 2026-07-13
"""
import json

import sqlalchemy as sa
from alembic import op

revision = "0101_schema_lib_drop_leaked_meta"
down_revision = "0100_feedback_followup"
branch_labels = None
depends_on = None

# Frozen mirror of schema_json._RESERVED_META / schema_architect._META_KEYS at write time,
# so this migration's behaviour never drifts if those constants later move.
_META_KEYS = {"required", "description", "type", "properties", "enum",
              "format", "items", "title", "examples", "default"}


def _clean(fields):
    """Return (cleaned_fields, changed). Drops reserved-metadata names carrying a NON-dict
    value (a leak, not a field). A genuine field named like metadata carries a dict
    definition and is kept."""
    if not isinstance(fields, dict):
        return fields, False
    cleaned = {k: v for k, v in fields.items()
               if not (k in _META_KEYS and not isinstance(v, dict))}
    return cleaned, len(cleaned) != len(fields)


def _run(bind) -> int:
    """Rewrite every schema_library row whose fields map carries leaked metadata keys.
    Returns the number of rows changed. Kept bind-explicit so it is unit-testable
    outside an Alembic runtime context."""
    rows = bind.execute(sa.text("SELECT pk, fields FROM schema_library")).fetchall()
    n = 0
    for pk, fields in rows:
        if isinstance(fields, str):  # be defensive if the driver hands back JSON text
            try:
                fields = json.loads(fields)
            except (ValueError, TypeError):
                continue
        cleaned, changed = _clean(fields)
        if changed:
            bind.execute(
                sa.text("UPDATE schema_library SET fields = CAST(:f AS JSONB), "
                        "updated_at = now() WHERE pk = :pk"),
                {"f": json.dumps(cleaned), "pk": pk})
            n += 1
    return n


def upgrade():
    n = _run(op.get_bind())
    print(f"[0101] cleaned leaked metadata keys from {n} schema_library row(s)")


def downgrade():
    # Non-reversible data cleanup — the dropped keys were junk metadata, nothing to restore.
    pass
