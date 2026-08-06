"""Tool · get_extracted_field · direct read from documents.extracted_fields JSONB.

Faster, more reliable, and less hallucinating than retrieval when the answer
is a typed field that the extractor already pulled (passport_no, aadhaar_no,
dob, address, invoice_total, etc).

`field` is a dotted path into the JSONB — e.g. "fields.aadhaar.aadhaar_no"
or "fields.passport_no". The path is walked safely; missing keys return
{found: false} rather than raising.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

NAME = "get_extracted_field"
DESCRIPTION = (
    "Read a typed value directly from the document's structured extracted_fields. "
    "Cheaper and more accurate than search_chunks when the value is already extracted. "
    "Args: field (dotted path, e.g. 'fields.aadhaar.aadhaar_no')."
)
PARAMS_SCHEMA = {
    "type": "object",
    "properties": {
        "field": {"type": "string", "description": "Dotted JSONB path to the field."},
    },
    "required": ["field"],
}


def call(*, db: Session, tenant_id: str, doc_id: str, field: str, **_: object) -> dict:
    from app.orm import Document

    doc = db.scalar(
        select(Document).where(
            Document.tenant_id == tenant_id,
            Document.id_external == doc_id,
        )
    )
    if doc is None:
        return {"found": False, "error": "doc not found"}

    ef = doc.extracted_fields or {}
    parts = [p for p in (field or "").split(".") if p]

    # Convenience · the agent often passes a flat field name (e.g.
    # "invoice_number") when the actual location is "fields.invoice_number".
    # Try the exact path first; if it misses AND the path doesn't already
    # start with "fields", retry with "fields." prefix.
    def _walk(obj: object, path_parts: list[str]):
        cur = obj
        for p in path_parts:
            if isinstance(cur, dict) and p in cur:
                cur = cur[p]
            else:
                return None, False
        return cur, True

    value, ok = _walk(ef, parts)
    if not ok and parts and parts[0] != "fields":
        # Retry with `fields.` prefix
        value, ok = _walk(ef, ["fields"] + parts)
        if ok:
            return {
                "found": True,
                "path": "fields." + ".".join(parts),
                "value": value,
                "doc_type": doc.doc_type,
            }

    if not ok:
        return {"found": False, "path": field, "available_keys": _top_keys(ef)}

    return {
        "found": True,
        "path": field,
        "value": value,
        "doc_type": doc.doc_type,
    }


def _top_keys(obj: object, depth: int = 0) -> list[str]:
    """Return up to 12 dotted-path keys from the top 2 levels of the
    JSONB so the LLM can recover when it asks for a missing path."""
    if not isinstance(obj, dict) or depth > 1:
        return []
    out: list[str] = []
    for k, v in list(obj.items())[:8]:
        out.append(k)
        if isinstance(v, dict):
            for k2 in list(v.keys())[:4]:
                out.append(f"{k}.{k2}")
    return out[:12]
