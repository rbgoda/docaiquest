"""M46 · learned_schemas repository — the self-learning extraction memory.

Tenant-scoped. `record()` accumulates the field labels + record kinds an
extraction found for a doc-type cluster; `hint_for()` turns the accumulated
counts into a prompt hint that nudges the next document of that type toward the
same (and more complete) fields. Field names only — never document content.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_current_tenant
from app.orm import LearnedSchema

# Move-1 PR1 · a small curated alias map so trivially-different labels for the
# same concept collapse to one cluster field ("rate" / "int_rate" →
# "interest_rate"). Deliberately conservative — heavier synonym/shape analysis
# is PR3's crystallization job, not this canonicalizer. Extend as clusters show
# recurring near-duplicates.
_LABEL_ALIASES = {
    "rate": "interest_rate",
    "int_rate": "interest_rate",
    "acct_number": "account_number",
    "acct_no": "account_number",
    "account_no": "account_number",
    "ref_no": "reference_number",
    "ref": "reference_number",
    "dob": "date_of_birth",
    "amt": "amount",
    "total_amount": "total",
    "amount_due": "total_due",
}


def canon_label(s: str | None) -> str | None:
    """Normalise a field label to a stable snake_case slug so the same concept
    accumulates under ONE cluster field. Strips leading articles, collapses
    non-alphanumerics, then folds a few known aliases. Returns None if empty."""
    s = (s or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    s = re.sub(r"^(the|a|an)_", "", s)
    if not s:
        return None
    return _LABEL_ALIASES.get(s, s)[:64]


_MAX_EXAMPLES = 8  # distinct example values kept per field (bounded)


def classify_value(v) -> str:
    """Coarse value type for schema inference: money | date | number | boolean | string.
    Used only on training-eligible (consented free) values."""
    s = str(v or "").strip()
    if not s:
        return "string"
    if s.lower() in ("yes", "no", "true", "false", "y", "n"):
        return "boolean"
    try:
        from app.graph.canonical import canon_date, canon_money
        amt, cur = canon_money(s)
        if amt is not None and (cur or any(sym in s for sym in "$€£₹¥")):
            return "money"
        if canon_date(s):
            return "date"
    except Exception:  # noqa: BLE001
        pass
    if re.fullmatch(r"-?\d{1,3}(?:,\d{3})*(?:\.\d+)?%?", s) or re.fullmatch(r"-?\d+(?:\.\d+)?%?", s):
        return "number"
    return "string"


def get(db: Session, doc_type: str | None) -> LearnedSchema | None:
    if not doc_type:
        return None
    return db.scalar(
        select(LearnedSchema).where(
            LearnedSchema.tenant_id == get_current_tenant(),
            LearnedSchema.doc_type == doc_type,
        )
    )


def hint_for(db: Session, doc_type: str | None, *, top_fields: int = 24, top_kinds: int = 6) -> str:
    """A one-paragraph hint from what we've learned about this doc_type, or ''
    when nothing's been learned yet (first document of its kind)."""
    row = get(db, doc_type)
    if row is None or (row.seen_count or 0) < 1:
        return ""
    fields = [k for k, _ in sorted((row.fields or {}).items(), key=lambda kv: -kv[1])][:top_fields]
    kinds = [k for k, _ in sorted((row.record_kinds or {}).items(), key=lambda kv: -kv[1])][:top_kinds]
    if not fields and not kinds:
        return ""
    parts = []
    if fields:
        parts.append("commonly-present fields: " + ", ".join(fields))
    if kinds:
        parts.append("record kinds: " + ", ".join(kinds))
    return (
        f"LEARNED HINT — across {row.seen_count} prior '{doc_type}' document(s) this workspace "
        f"has seen, the following recur → " + " · ".join(parts) + ". Extract every one that is "
        "present in THIS document (plus anything new you find). Do not invent values not in the text."
    )


def record(db: Session, doc_type: str | None, field_labels, record_kinds, examples=None) -> None:
    """Merge what an extraction found into the learned schema for this cluster.
    Increments per-label / per-kind counts + seen_count. `examples` (a
    {raw_label: [values]} map) is passed ONLY for training-eligible (consented
    free-plan) documents — it accumulates per-label value-type tallies + a small
    capped set of distinct example values to drive typed crystallization. Best-effort."""
    if not doc_type:
        return
    row = get(db, doc_type)
    if row is None:
        row = LearnedSchema(
            tenant_id=get_current_tenant(), doc_type=doc_type,
            fields={}, record_kinds={}, field_examples={}, seen_count=0,
        )
        db.add(row)
    fields = dict(row.fields or {})
    for lab in field_labels:
        lab = canon_label(lab)
        if lab:
            fields[lab] = fields.get(lab, 0) + 1
    kinds = dict(row.record_kinds or {})
    for k in record_kinds:
        k = canon_label(k)
        if k:
            kinds[k] = kinds.get(k, 0) + 1
    # Move-1 (b) · value/type examples — training-eligible docs only.
    if examples:
        fex = dict(row.field_examples or {})
        for raw_lab, vals in (examples or {}).items():
            lab = canon_label(raw_lab)
            if not lab:
                continue
            entry = dict(fex.get(lab) or {"types": {}, "values": []})
            types = dict(entry.get("types") or {})
            values = list(entry.get("values") or [])
            for v in (vals or []):
                sv = str(v or "").strip()
                if not sv:
                    continue
                t = classify_value(sv)
                types[t] = types.get(t, 0) + 1
                if sv not in values and len(values) < _MAX_EXAMPLES:
                    values.append(sv[:64])
            entry["types"], entry["values"] = types, values
            fex[lab] = entry
        row.field_examples = fex
    # Reassign (not mutate) so SQLAlchemy flags the JSONB columns dirty.
    row.fields = fields
    row.record_kinds = kinds
    row.seen_count = (row.seen_count or 0) + 1
    row.updated_at = datetime.now(timezone.utc)
    db.flush()
