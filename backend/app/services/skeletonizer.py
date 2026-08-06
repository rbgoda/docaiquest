"""M44.P13 PR1 · Skeletonizer — the privacy barrier for federated learning.

Transforms a LOCAL "understanding" row into a value-free, PII-free, tenant-
anonymous SKELETON that is safe to contribute to the global knowledge pool —
or REFUSES (returns ``None``) when it cannot guarantee that. This is the
load-bearing barrier the whole federated-learning design rests on
promotion is a *transform*, never
a copy. We keep only generalizable structure — doc_type tokens, field NAMES,
agent tool sequences, intent-templated questions — and drop every value,
identifier, entity, document id, and tenant id.

Defense in depth — two independent checks before any skeleton is emitted:
  1. structural allow-list — only known-safe fields are copied; nothing else
     rides along.
  2. PII assertion — every free-text field is run through ``app.pii.redact``
     and the skeleton is REFUSED if any redaction placeholder appears.
Plus the project-wide human backstop: nothing a skeleton produces goes
*active* in the global pool without superadmin curation (design decision #3).

Nothing here is wired into a running job yet. PR1 ships the barrier + tests;
later PRs add the nightly ``knowledge_promoter`` and the control-plane
``/api/platform/knowledge/*`` endpoints that call it.

Known limitation (documented for the promoter PR): a question template that
was lowercased by P10 could in theory hide a bare first-name that neither the
identifier gate (which keys on capitalization) nor Tier-1 PII regex catches.
The agent_skill path is therefore deliberately conservative, and superadmin
curation is the required second gate before such a skeleton is ever served.
"""

from __future__ import annotations

from app.pii import redact
from app.services.learning_promoter import has_doc_specific_identifier

SUPPORTED_KINDS = ("extraction_correction", "agent_skill", "generated_schema")

# The primitive field types a crystallized schema may carry to the global pool.
# Anything else is coerced to "string" — the skeleton never carries values, only
# field NAMES + a primitive type tag.
_SCHEMA_FIELD_TYPES = frozenset({"string", "number", "integer", "date", "boolean"})
_MAX_SCHEMA_FIELDS = 40


def _has_pii(text: str) -> bool:
    """True if app.pii flags anything in ``text`` (any placeholder emitted)."""
    if not text:
        return False
    return bool(redact(text).mapping)


def _clean_doc_type(doc_type: str | None) -> str | None:
    """doc_type is a controlled-vocab token (``insurance_certificate`` …).
    Normalize + reject anything that smells like a free-form value leaked in."""
    if not doc_type or not isinstance(doc_type, str):
        return None
    dt = doc_type.strip().lower()
    if not dt or " " in dt or _has_pii(dt) or has_doc_specific_identifier(dt):
        return None
    return dt[:64]


def skeletonize_extraction_correction(doc_type, pattern) -> dict | None:
    """Keep only the field-NAME structure of a correction pattern
    (``{wrong_field, should_be}``) — field paths, no values."""
    dt = _clean_doc_type(doc_type)
    if dt is None or not isinstance(pattern, dict):
        return None
    skel_pattern: dict[str, str] = {}
    for key in ("wrong_field", "should_be"):
        val = pattern.get(key)
        if val is None:
            continue
        if not isinstance(val, str) or _has_pii(val):
            return None
        skel_pattern[key] = val[:128]
    if "wrong_field" not in skel_pattern:
        return None
    return {"kind": "extraction_correction", "doc_type": dt, "pattern": skel_pattern}


def skeletonize_agent_skill(doc_type, question_template, tool_sequence) -> dict | None:
    """Keep the (already-anonymized) intent template + the tool sequence."""
    dt = _clean_doc_type(doc_type)
    if dt is None:
        return None
    if not isinstance(question_template, str):
        return None
    q = question_template.strip()
    # The template must already be de-identified (P10 anonymizes numbers /
    # emails / dates and lowercases). Refuse if it still carries an identifier
    # or trips a PII pattern.
    if not q or has_doc_specific_identifier(q) or _has_pii(q):
        return None
    if not isinstance(tool_sequence, list) or not tool_sequence:
        return None
    seq = [t[:64] for t in tool_sequence if isinstance(t, str) and t]
    if not seq:
        return None
    return {
        "kind": "agent_skill",
        "doc_type": dt,
        "question_template": q[:512],
        "tool_sequence": seq,
    }


def skeletonize_generated_schema(doc_type, fields) -> dict | None:
    """Move-1 PR4 · reduce a crystallized GeneratedSchema to a value-free
    skeleton: the cluster's doc_type + its field NAMES and primitive types. A
    GeneratedSchema is already values-free by construction (field names only), but
    we still PII-gate every label (defense in depth) and DROP any that trips it.
    Returns None if nothing safe remains."""
    dt = _clean_doc_type(doc_type)
    if dt is None or not isinstance(fields, dict):
        return None
    skel_fields: dict[str, str] = {}
    for label, spec in fields.items():
        if not isinstance(label, str):
            continue
        lab = label.strip()
        # A field NAME must never carry PII/identifiers — drop it if it does.
        if not lab or _has_pii(lab) or has_doc_specific_identifier(lab):
            continue
        typ = spec.get("type") if isinstance(spec, dict) else None
        if typ not in _SCHEMA_FIELD_TYPES:
            typ = "string"
        skel_fields[lab[:64]] = typ
        if len(skel_fields) >= _MAX_SCHEMA_FIELDS:
            break
    if not skel_fields:
        return None
    return {"kind": "generated_schema", "doc_type": dt, "fields": skel_fields}


def skeletonize(kind: str, **kw) -> dict | None:
    """Dispatch to the per-kind skeletonizer. Returns a value-free skeleton
    dict (no tenant_id, no doc ids, no values) or ``None`` to refuse.

    Callers MUST treat ``None`` as "do not contribute this row" — never fall
    back to sending the raw row.
    """
    if kind == "extraction_correction":
        return skeletonize_extraction_correction(kw.get("doc_type"), kw.get("pattern"))
    if kind == "agent_skill":
        return skeletonize_agent_skill(
            kw.get("doc_type"), kw.get("question_template"), kw.get("tool_sequence")
        )
    if kind == "generated_schema":
        return skeletonize_generated_schema(kw.get("doc_type"), kw.get("fields"))
    return None
