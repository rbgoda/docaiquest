"""Move-1 PR3 · schema crystallizer — distil stable LearnedSchema clusters into
concrete GeneratedSchema rows.

The universal extractor accumulates, per detected_doc_type cluster, a
`{field_label: times_seen}` map + `seen_count` (see repositories/learned_schemas).
Once a cluster has been seen enough times with a consistent CORE set of labels,
this promotes those labels into a concrete typed schema. The extractor (PR3b)
then merges them onto the universal base so recurring types get first-class,
consistently-named fields instead of a loose key_facts bag.

Field-NAMES only — never document values (same privacy discipline as the rest of
the learning loop). The DB driver is the nightly `jobs/schema_crystallize` task.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.orm import GeneratedSchema, LearnedSchema

log = logging.getLogger("docaiq.schema_crystallizer")

# Universal base scalars — never promote these (they're already first-class on the
# universal schema, so promoting would be a no-op collision). Belt-and-suspenders:
# PR1 already records only the distinctive labeled-array labels.
_BASE_KEYS = frozenset({
    "detected_doc_type", "detected_doc_subtype", "title", "issuer",
    "issuer_address", "subject_or_recipient", "parties", "primary_date",
    "dates", "primary_amount", "amounts", "identifiers", "key_facts",
    "key_text_points", "tags", "records", "summary",
})
_MAX_FIELDS = 40  # cap a crystallized schema so a pathological cluster can't bloat it


def select_core_fields(fields: dict, seen_count: int, *, ratio: float) -> list[str]:
    """The stable CORE labels of a cluster: those present in ≥ `ratio` of the
    docs seen (count / seen_count), most-frequent first, base keys excluded,
    capped. Pure — no DB. Returns [] when nothing qualifies."""
    if not fields or seen_count < 1:
        return []
    thresh = max(1, math.ceil(ratio * seen_count))  # "present in ≥ ratio of docs"
    core = [
        (lab, cnt) for lab, cnt in (fields or {}).items()
        if lab and lab not in _BASE_KEYS and isinstance(cnt, (int, float)) and cnt >= thresh
    ]
    core.sort(key=lambda kv: -kv[1])
    return [lab for lab, _ in core[:_MAX_FIELDS]]


# JSON-schema type + descriptor for each inferred value type (money/date map to
# string with a hint, since the extractor tool speaks JSON-schema types).
_TYPE_SPEC = {
    "number":  ("number",  "numeric value"),
    "money":   ("string",  "amount with currency, e.g. 'USD 1,250.00'"),
    "date":    ("string",  "date, YYYY-MM-DD when possible"),
    "boolean": ("boolean", "true/false"),
    "string":  ("string",  "text value"),
}
_DOMINANT_SHARE = 0.6   # a type must own ≥60% of examples to be inferred
_ENUM_MAX = 6           # ≤ this many distinct values → treat as an enum


def _infer_field(lab: str, ex: dict | None) -> dict:
    """Infer a typed property for one label from its accumulated examples. Falls
    back to a plain string when there aren't enough examples to be confident."""
    base_desc = (f"The document's {lab.replace('_', ' ')} — a field that recurs "
                 "across documents of this type. Empty string if not present here.")
    types = (ex or {}).get("types") or {}
    values = (ex or {}).get("values") or []
    total = sum(types.values())
    # Enum: a small, stable set of distinct string-ish values.
    if 0 < len(values) <= _ENUM_MAX and total >= 3 and (types.get("string", 0) >= total * 0.5):
        return {"type": "string", "enum": list(values),
                "description": base_desc + " One of the known values."}
    if total >= 3:
        top = max(types, key=types.get)
        if types[top] >= total * _DOMINANT_SHARE and top in _TYPE_SPEC:
            jtype, hint = _TYPE_SPEC[top]
            return {"type": jtype, "description": f"{base_desc} ({hint})"}
    return {"type": "string", "description": base_desc}


def build_typed_fields(labels: list[str], field_examples: dict | None = None) -> dict:
    """Turn core labels into a universal-mergeable typed-property map. Uses the
    accumulated value examples (training-eligible docs only) to infer each field's
    type/enum; falls back to string when there's no signal — so paid-only clusters
    (no examples) stay string, exactly as before."""
    fex = field_examples or {}
    return {lab: _infer_field(lab, fex.get(lab)) for lab in labels}


def crystallize_tenant(db: Session, tenant_id: str) -> dict:
    """Crystallize every eligible cluster for one tenant. Upserts GeneratedSchema
    rows (status='active') and returns stats. Caller sets tenant scope + commits.
    Best-effort per cluster — one bad cluster never aborts the rest."""
    s = get_settings()
    min_docs = s.schema_crystallize_min_docs
    ratio = s.schema_crystallize_core_ratio
    stats = {"clusters": 0, "crystallized": 0, "skipped": 0}

    rows = db.scalars(
        select(LearnedSchema).where(
            LearnedSchema.tenant_id == tenant_id,
            LearnedSchema.seen_count >= min_docs,
        )
    ).all()
    for ls in rows:
        stats["clusters"] += 1
        try:
            core = select_core_fields(ls.fields or {}, ls.seen_count or 0, ratio=ratio)
            if not core:
                stats["skipped"] += 1
                continue
            typed = build_typed_fields(core, getattr(ls, "field_examples", None))
            gen = db.scalar(select(GeneratedSchema).where(
                GeneratedSchema.tenant_id == tenant_id,
                GeneratedSchema.cluster_key == ls.doc_type,
            ))
            if gen is None:
                gen = GeneratedSchema(
                    tenant_id=tenant_id, cluster_key=ls.doc_type,
                    label=ls.doc_type.replace("_", " ").title(),
                    fields=typed, status="active", source="crystallize",
                    seen_count=ls.seen_count or 0,
                )
                db.add(gen)
            elif gen.status != "rejected":  # never resurrect an operator-rejected schema
                gen.fields = typed
                gen.seen_count = ls.seen_count or 0
                gen.status = "active"
                gen.updated_at = datetime.now(timezone.utc)
            else:
                stats["skipped"] += 1
                continue
            stats["crystallized"] += 1
        except Exception:  # noqa: BLE001 — one bad cluster must not abort the tenant
            log.warning("crystallize: cluster %r failed for tenant %s", ls.doc_type, tenant_id)
            stats["skipped"] += 1
    db.flush()
    return stats
