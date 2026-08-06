"""Auto-triage for product feedback (ported from chataiq/triage.py).

Runs in a FastAPI BackgroundTask off the submit path — never blocks or breaks the
user's submission (which is already stored). Best-effort: a missing/failing LLM
degrades to a no-op and the row simply stays 'new' for manual review.

  1. ask the LLM to classify severity/area + draft a 1-2 sentence resolution,
  2. write that draft into the feedback row's resolution note. Status stays 'new'
     (auto-triage is a suggestion, not "work started") — a human flips it to
     in_progress when they pick it up.

Admin notification is console-only (the superadmin 'open feedback' KPI + inbox) —
no Telegram/email push, by product decision.
"""
from __future__ import annotations

import json
import logging
import os

from app.model_registry import REGISTRY as _AI_REGISTRY

log = logging.getLogger("docaiq.feedback_triage")

# Reuse the classifier's model knob so triage runs on whatever is configured +
# working for this deployment (prod = dashscope/qwen-vl-max; OpenRouter is depleted).
_DIRECT_PREFIXES = ("openrouter/", "dashscope/", "google/")


def _model() -> str:
    m = os.getenv("DOCAIQ_CLASSIFIER_MODEL") or _AI_REGISTRY["classification"].default_model
    return m if m.startswith(_DIRECT_PREFIXES) else f"openrouter/{m}"


def _parse_json(text: str) -> dict | None:
    if not text:
        return None
    t = text.strip()
    if t.startswith("```"):
        t = t.split("```", 2)[1] if t.count("```") >= 2 else t.strip("`")
        t = t[4:] if t.lower().startswith("json") else t
    i, j = t.find("{"), t.rfind("}")
    if i == -1 or j == -1 or j < i:
        return None
    try:
        d = json.loads(t[i:j + 1])
        return d if isinstance(d, dict) else None
    except (ValueError, TypeError):
        return None


def classify_and_draft(fb: dict, tenant_id: str | None) -> dict | None:
    """Return {severity, area, resolution} or None if no usable LLM/response."""
    from app.llm import gateway
    from app.llm.prompts import get_prompt
    system = get_prompt("feedback_triage")
    user = (
        f"Category: {fb.get('category', 'general')}\n"
        f"Rating: {fb.get('rating') or '-'}/5\n"
        f"Feedback: {fb.get('comments', '')}\n"
        f"Suggestion: {fb.get('suggestion') or '-'}\n"
        f"Page: {fb.get('page') or '-'}"
    )
    try:
        result = gateway.call(
            model=_model(),
            messages=[gateway.Message(role="system", content=system),
                      gateway.Message(role="user", content=user)],
            max_tokens=320,
            temperature=0.2,
            tenant_id=tenant_id,
            task_kind="triage",
        )
    except Exception as e:  # noqa: BLE001 — never let triage break anything
        log.warning("triage LLM call failed: %s", e)
        return None
    data = _parse_json(result.text or "")
    if not data or not str(data.get("resolution", "")).strip():
        return None
    return {
        "severity": str(data.get("severity", "medium")).lower()[:10] or "medium",
        "area": str(data.get("area", "general"))[:40] or "general",
        "resolution": str(data.get("resolution", "")).strip()[:900],
    }


def triage_feedback(feedback_pk: int, tenant_id: str | None) -> None:
    """Best-effort background triage: draft a resolution note; status stays 'new'."""
    from app.db import SessionLocal, set_current_tenant
    from app.orm import ProductFeedback
    if tenant_id:
        try:
            set_current_tenant(tenant_id)
        except Exception:  # noqa: BLE001
            pass
    db = SessionLocal()
    try:
        row = db.get(ProductFeedback, feedback_pk)
        if row is None or row.status != "new":
            return
        fb = {"category": row.category, "rating": row.rating, "comments": row.comments,
              "suggestion": row.suggestion, "page": row.page}
        draft = classify_and_draft(fb, tenant_id)
        if not draft:
            return
        # Re-load inside the write to avoid clobbering a manual change mid-flight.
        row = db.get(ProductFeedback, feedback_pk)
        if row is None or row.status != "new":
            return
        # Draft a resolution suggestion but LEAVE status 'new'. Auto-triage is not
        # "work started" — flipping to in_progress made every incoming item look
        # actively worked, hiding the real untouched queue (feedback pk 44). A human
        # moves it to in_progress when they actually pick it up.
        row.resolution = f"🤖 Auto-triage ({draft['severity']} · {draft['area']}): {draft['resolution']}"
        db.commit()
        log.info("triaged feedback #%s → %s · %s", feedback_pk, draft["severity"], draft["area"])
    except Exception as e:  # noqa: BLE001 — triage must never crash the app
        log.warning("triage_feedback #%s failed: %s", feedback_pk, e)
    finally:
        db.close()
