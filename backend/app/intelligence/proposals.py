"""Intelligence Dashboard · Phase C — AI-proposed views.

Builds a values-FREE profile of the user's corpus (doc types + counts + the
field *names* each type carries — never any values) and asks the LLM to assemble
useful View specs from it. The proposal is privacy-clean (no PII can leak: only
schema goes to the model) and cheap (one small call, cached in saved_views).
"""
from __future__ import annotations

import json
import logging

from app.config import get_settings
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.intelligence import alerts as _alerts
from app.intelligence.view_engine import BUILTIN_VIEWS
from app.llm import gateway
from app.orm import Document, SavedView

log = logging.getLogger("docaiq.intelligence.proposals")

# DashScope Qwen-Max by default (matches compose DOCAIQ_INTELLIGENCE_MODEL).
# Override with DOCAIQ_INTELLIGENCE_MODEL to route the (cheap, occasional)
# proposal call at whichever provider has credit.
_MODEL = get_settings().intelligence_model
_MAX_FIELDS_PER_TYPE = 18
_LABELED = ("key_facts", "identifiers", "dates", "amounts", "parties")


def build_corpus_profile(db: Session, *, tenant_id: str, owner_user_id: int) -> list[dict]:
    """Per doc-type: count + the set of field NAMES seen (flat keys + labeled-
    array labels). No values — schema only."""
    docs = db.scalars(select(Document).where(
        Document.tenant_id == tenant_id, Document.owner_user_id == owner_user_id,
        Document.is_archived.is_(False))).all()
    by_type: dict[str, dict] = {}
    for d in docs:
        if (d.ingestion_status or "") != "ready":
            continue
        t = d.doc_type or "unclassified"
        rec = by_type.setdefault(t, {"type": t, "count": 0, "fields": set()})
        rec["count"] += 1
        fields = _alerts._inner_fields(d.extracted_fields)
        for k, v in fields.items():
            if k in _LABELED:
                for it in (v or []):
                    if isinstance(it, dict) and it.get("label"):
                        rec["fields"].add(str(it["label"])[:40])
            elif isinstance(v, (str, int, float)):
                rec["fields"].add(k)
    out = []
    for rec in by_type.values():
        out.append({"type": rec["type"], "count": rec["count"],
                    "fields": sorted(rec["fields"])[:_MAX_FIELDS_PER_TYPE]})
    out.sort(key=lambda r: -r["count"])
    return out


_SYSTEM = (
    "You design 'views' for a personal document dashboard. A view groups one or "
    "more document types into a useful table — e.g. an invoices/AP view, an "
    "ID-and-expiry tracker, a contracts view. You are given ONLY the document "
    "types present, their counts, and the field NAMES available (never values). "
    "Return STRICT JSON: {\"views\":[{...}]}. Each view: "
    "{\"id\":kebab-slug, \"title\":short, \"icon\":single emoji, \"subtitle\":short, "
    "\"docTypes\":[type strings from the profile], "
    "\"columns\":[{\"label\":str,\"get\":[candidate field names]} OR {\"label\":str,\"deadline\":true}], "
    "\"metrics\":[{\"label\":str,\"kind\":\"count\"|\"past\"|\"soon\"}]}. "
    "Use a 'deadline' column + 'soon'/'past' metrics only when a type has an "
    "expiry/due/end date field. 3-6 columns each. Propose 2-4 views that are NOT "
    "already covered by these built-in ids: {builtins}. Output ONLY the JSON."
)


def _sanitize(view: dict, valid_types: set[str]) -> dict | None:
    """Coerce one LLM-proposed view into a safe view_engine spec, or drop it."""
    if not isinstance(view, dict):
        return None
    doc_types = [str(t) for t in (view.get("docTypes") or []) if str(t) in valid_types]
    if not doc_types:
        return None
    cols = []
    for c in (view.get("columns") or []):
        if not isinstance(c, dict) or not c.get("label"):
            continue
        if c.get("deadline"):
            cols.append({"label": str(c["label"])[:40], "deadline": True})
        else:
            gets = [str(g) for g in (c.get("get") or []) if isinstance(g, (str, int))][:6]
            if gets:
                cols.append({"label": str(c["label"])[:40], "get": gets})
    if not cols:
        return None
    metrics = []
    for m in (view.get("metrics") or []):
        if isinstance(m, dict) and m.get("kind") in ("count", "past", "soon") and m.get("label"):
            metrics.append({"label": str(m["label"])[:24], "kind": m["kind"]})
    if not metrics:
        metrics = [{"label": "Documents", "kind": "count"}]
    slug = str(view.get("id") or view.get("title") or "view").lower()
    slug = "".join(ch if ch.isalnum() else "-" for ch in slug).strip("-")[:48] or "view"
    icon = str(view.get("icon") or "📁")[:4]
    return {"id": slug, "title": str(view.get("title") or slug)[:48], "icon": icon,
            "subtitle": str(view.get("subtitle") or "")[:80] or None,
            "docTypes": doc_types, "columns": cols[:6], "metrics": metrics[:4], "sort": "deadline"}


def propose_views(db: Session, *, tenant_id: str, owner_user_id: int,
                  user_email: str | None = None) -> dict:
    """Build the profile, ask the LLM for views, validate, upsert as saved
    (source='ai'). Returns {created, profileTypes}."""
    profile = build_corpus_profile(db, tenant_id=tenant_id, owner_user_id=owner_user_id)
    valid_types = {p["type"] for p in profile}
    if not profile:
        return {"created": 0, "profileTypes": 0, "reason": "no ready documents"}

    builtins = ", ".join(v["id"] for v in BUILTIN_VIEWS)
    system = _SYSTEM.replace("{builtins}", builtins)
    user_msg = "Document profile:\n" + json.dumps(profile, ensure_ascii=False)

    try:
        result = gateway.call(
            _MODEL,
            [gateway.Message(role="system", content=system),
             gateway.Message(role="user", content=user_msg)],
            temperature=0.2, max_tokens=1200, structured=True,
            tenant_id=tenant_id, user_email=user_email, task_kind="intelligence_propose",
        )
    except Exception as e:  # noqa: BLE001 — proposal is best-effort, never fatal
        log.warning("propose_views: LLM call failed: %s", e)
        return {"created": 0, "profileTypes": len(profile), "reason": "llm_unavailable"}

    parsed = result.structured if result.structured is not None else _loads(result.text)
    raw_views = (parsed or {}).get("views") if isinstance(parsed, dict) else None
    if not isinstance(raw_views, list):
        return {"created": 0, "profileTypes": len(profile), "reason": "no_views"}

    builtin_ids = {v["id"] for v in BUILTIN_VIEWS}
    created = 0
    for rv in raw_views[:6]:
        spec = _sanitize(rv, valid_types)
        if spec is None or spec["id"] in builtin_ids:
            continue
        existing = db.scalar(select(SavedView).where(
            SavedView.tenant_id == tenant_id, SavedView.owner_user_id == owner_user_id,
            SavedView.view_key == spec["id"]))
        if existing is not None:
            if not existing.dismissed:        # refresh spec, respect a prior dismiss
                existing.spec = spec
            continue
        db.add(SavedView(tenant_id=tenant_id, owner_user_id=owner_user_id,
                         view_key=spec["id"], spec=spec, source="ai"))
        created += 1
    db.commit()
    log.info("propose_views: owner=%s created=%s types=%s", owner_user_id, created, len(profile))
    return {"created": created, "profileTypes": len(profile)}


def _loads(text: str | None):
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:  # noqa: BLE001
        # tolerate ```json fences
        t = text.strip().lstrip("`").replace("json", "", 1)
        try:
            return json.loads(t[t.index("{"):t.rindex("}") + 1])
        except Exception:  # noqa: BLE001
            return None
