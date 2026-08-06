"""Adaptive Schema Loop — the extraction brain heals and improves itself.

When a document is UNDERSERVED (no typed schema, or typed but poorly extracted), autopilot escalates
to a stronger model to draft a schema, stores it as `proposed` for HITL review, and links the doc as
the exemplar. When a human edits/adds a field on a doc, that correction flows BACK into the schema
(`learn_field`) so every future doc of that type benefits.

    DETECT (underserved) → DRAFT (escalate → propose) → REVIEW (HITL) → APPROVE (auto-re-extract,
    handled by the on-approval trigger) → LEARN (field edits → schema) → repeat.

Detection is coverage-gated, not just type-gated: a doc with a schema that extracts poorly is
underserved too. Escalation is tiered: only underserved docs pay for the stronger model.
"""
from __future__ import annotations

import logging

from sqlalchemy import select

from app.config import get_settings
from app.db import get_current_tenant
from app.orm import DocumentChunk, SchemaLibrary
from app.agents import schema_architect
from app.agents.fact_extractor import _resolve_schema_slug

log = logging.getLogger("docaiq.autopilot")

_NO_SCHEMA_TYPES = {"other", "unknown", "", None}  # catch-alls — no meaningful per-type schema


def _approved_schema(db, doc_type: str) -> "SchemaLibrary | None":
    slug = _resolve_schema_slug(db, doc_type or "")
    if not slug:
        return None
    return db.scalar(select(SchemaLibrary).where(
        SchemaLibrary.tenant_id == get_current_tenant(),
        SchemaLibrary.type_slug == slug,
        SchemaLibrary.status == "approved",
    ).order_by(SchemaLibrary.version.desc()))


def coverage(fields: dict, schema_field_names: set[str]) -> float:
    """Fraction of the schema's fields that actually got a non-empty value."""
    if not schema_field_names:
        return 0.0
    filled = sum(1 for k in schema_field_names if (fields or {}).get(k) not in (None, "", [], {}))
    return filled / len(schema_field_names)


def assess(db, doc) -> dict:
    """Is this doc underserved? → {underserved, reason, coverage, has_schema, slug}.

    Underserved when: no resolvable schema · extracted on the universal envelope · or typed but
    field-coverage below the threshold (schema exists but the doc extracts poorly)."""
    s = get_settings()
    ef = doc.extracted_fields or {}
    envelope = ef.get("doc_type")
    fields = ef.get("fields") or {}
    schema = _approved_schema(db, doc.doc_type or "")
    if schema is None:
        return {"underserved": True, "reason": "no_schema", "coverage": 0.0,
                "has_schema": False, "slug": None}
    if envelope == "universal":
        return {"underserved": True, "reason": "universal_extracted", "coverage": 0.0,
                "has_schema": True, "slug": schema.type_slug}
    cov = coverage(fields, set((schema.fields or {}).keys()))
    if cov < s.schema_autopilot_min_coverage:
        return {"underserved": True, "reason": f"low_coverage_{cov:.2f}", "coverage": cov,
                "has_schema": True, "slug": schema.type_slug}
    return {"underserved": False, "reason": "ok", "coverage": cov,
            "has_schema": True, "slug": schema.type_slug}


def _full_text(db, doc, cap: int = 14000) -> str:
    chunks = db.scalars(select(DocumentChunk).where(DocumentChunk.document_pk == doc.pk)
                        .order_by(DocumentChunk.chunk_index)).all()
    return "\n".join(c.text or "" for c in chunks)[:cap]


def autopilot_draft(db, doc, created_by: str = "autopilot") -> dict | None:
    """Escalate to a stronger model, draft a schema from the FULL doc, store it `proposed` for HITL
    review with the doc linked as the exemplar. Returns a summary (or None if nothing to draft)."""
    from app.routers.superadmin import _store_drafted_schema
    s = get_settings()
    slug = doc.doc_type or "other"
    if slug in _NO_SCHEMA_TYPES:
        return None
    # Don't re-propose if an unreviewed autopilot draft for this type already exists.
    dup = db.scalar(select(SchemaLibrary).where(
        SchemaLibrary.tenant_id == get_current_tenant(), SchemaLibrary.type_slug == slug,
        SchemaLibrary.status == "proposed", SchemaLibrary.source == "autopilot"))
    if dup is not None:
        return {"slug": slug, "skipped": "draft_already_pending", "schema_pk": dup.pk}
    model = getattr(s, "schema_autopilot_model", "") or s.strong_extract_model
    try:
        drafted = schema_architect.draft_schema(db, type_slug=slug,
                                                sample_text=_full_text(db, doc), model=model)
    except Exception as e:  # noqa: BLE001
        log.warning("autopilot_draft: draft failed for %s: %s", slug, e)
        return None
    row = _store_drafted_schema(db, get_current_tenant(), slug, drafted, created_by, source="autopilot")
    row.sample_doc_pk = doc.pk
    db.commit()
    log.info("autopilot_draft: proposed schema '%s' (%d fields, conf=%s) exemplar doc pk=%s",
             slug, len(drafted.get("fields") or {}), drafted.get("confidence"), doc.pk)
    return {"slug": slug, "fields": len(drafted.get("fields") or {}),
            "confidence": drafted.get("confidence"), "schema_pk": row.pk, "status": row.status}


def _infer_field_type(v) -> str:
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, (int, float)):
        return "number"
    if isinstance(v, list):
        return "array"
    if isinstance(v, dict):
        return "object"
    return "string"


def _bootstrap_draft_from_doc(db, doc, slug: str, new_field: str, field_type: str,
                              description: str | None) -> bool:
    """Type has only a BUILT-IN schema (no library row) → seed a `proposed` draft from the doc's
    CURRENT extracted fields + the human-added field, into the HITL queue. On approval the extractor
    routes this type to the library schema and every doc of the type gains the field. This is what
    makes a correction on a built-in type actually teach that type (Adaptive Schema Loop)."""
    from app.routers.superadmin import _store_drafted_schema
    cur = (doc.extracted_fields or {}).get("fields") or {}
    fields: dict = {}
    for k, v in cur.items():
        if isinstance(k, str) and not k.startswith("_"):
            fields[k] = {"type": _infer_field_type(v), "description": k.replace("_", " ")}
    fields[new_field] = {"type": field_type,
                         "description": description or f"{new_field} (added during review)"}
    label = (doc.doc_type or slug).replace("_", " ").title()
    drafted = {
        "label": label, "domain": None, "fields": fields,
        "description": f"Schema for {label}, seeded from a reviewer's field addition.",
        "confidence": None, "model": None,
        "rationale": f"Bootstrapped when '{new_field}' was added during review "
                     f"(exemplar doc {getattr(doc, 'id_external', doc.pk)}). Reviewer approves to apply to all {label} docs.",
    }
    row = _store_drafted_schema(db, get_current_tenant(), slug, drafted,
                                created_by="hitl_field_add", source="hitl_bootstrap")
    row.sample_doc_pk = doc.pk
    db.commit()
    log.info("learn_field: bootstrapped proposed schema '%s' (%d fields) from field-add on doc pk=%s",
             slug, len(fields), doc.pk)
    return True


def learn_field(db, doc, field_name: str, field_type: str = "string",
                description: str | None = None) -> bool:
    """HITL feedback: a human added `field_name` on `doc`. Teach the TYPE's schema so every future
    doc of that type captures it:
      · approved library schema exists → add the field in place (takes effect immediately);
      · a proposed draft exists → add the field to that draft (approved together at review);
      · NEITHER (type runs on a built-in code schema) → BOOTSTRAP a proposed draft from the doc's
        current fields + this field, into the HITL queue.
    Returns True if a schema/draft changed. Best-effort — a failed learn never breaks the field edit."""
    try:
        if not field_name or field_name.startswith("__"):
            return False
        slug = _resolve_schema_slug(db, doc.doc_type or "") or (doc.doc_type or "").strip().lower()
        if not slug or slug in _NO_SCHEMA_TYPES:
            return False
        tid = get_current_tenant()

        # 1. Approved schema → add the field in place.
        schema = _approved_schema(db, doc.doc_type or "")
        if schema is not None:
            fields = dict(schema.fields or {})
            if field_name in fields:
                return False
            fields[field_name] = {"type": field_type,
                                  "description": description or f"{field_name} (added during review)"}
            schema.fields = fields
            schema.notes = ((schema.notes or "") + f" +HITL:{field_name}").strip()
            db.commit()
            log.info("learn_field: added '%s' to approved schema '%s' v%s",
                     field_name, schema.type_slug, schema.version)
            return True

        # 2. A pending proposed draft → augment it (so it's included when approved).
        draft = db.scalar(select(SchemaLibrary).where(
            SchemaLibrary.tenant_id == tid, SchemaLibrary.type_slug == slug,
            SchemaLibrary.status == "proposed").order_by(SchemaLibrary.version.desc()))
        if draft is not None:
            fields = dict(draft.fields or {})
            if field_name in fields:
                return False
            fields[field_name] = {"type": field_type,
                                  "description": description or f"{field_name} (added during review)"}
            draft.fields = fields
            draft.notes = ((draft.notes or "") + f" +HITL:{field_name}").strip()
            db.commit()
            log.info("learn_field: added '%s' to proposed draft '%s' v%s", field_name, slug, draft.version)
            return True

        # 3. Only a built-in schema exists → bootstrap a proposed draft for HITL approval.
        return _bootstrap_draft_from_doc(db, doc, slug, field_name, field_type, description)
    except Exception as e:  # noqa: BLE001
        db.rollback()
        log.warning("learn_field: failed to add '%s': %s", field_name, e)
        return False
