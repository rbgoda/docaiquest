"""Intelligence Dashboard · Phase A — the alert engine (zero-LLM).

Evaluates built-in attention rules over a user's documents using only data we
already have: `documents.extracted_fields` (shape `{doc_type, fields, ...}`),
`doc_type` / `doc_type_confidence`, and `ingestion_status`. Pure compute — no
LLM, nothing leaves the box.

Date-field detection mirrors the curated field names used by
app/services/doc_chat._find_expiry_date (kept independent here so the alert
path carries no chat/LLM import weight).
"""
from __future__ import annotations

import datetime as _dt
import re as _re

# Window (days) within which an upcoming deadline is "soon" rather than fine.
SOON_DAYS = 30
# Below this classification confidence (or unclassified) a ready doc is flagged
# for review.
LOW_CONFIDENCE = 0.60

# Field names that typically hold a deadline. "due"-flavored keys are payment
# deadlines (invoices); the rest are validity/expiry.
_DEADLINE_KEYS = [
    "expiry_date", "valid_until", "valid_thru", "valid_till",
    "expiration_date", "date_of_expiry", "expires_on",
    "policy_end_date", "coverage_end_date", "end_date",
    "due_date", "payment_due_date", "due",
]
_PAYMENT_RE = _re.compile(r"due|payment", _re.I)

_DATE_FMTS = [
    "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%m-%d-%Y",
    "%d %b %Y", "%d %B %Y", "%b %d, %Y", "%B %d, %Y", "%d-%b-%y", "%d-%B-%y",
]


def _parse_date(s) -> _dt.date | None:
    if not s or not isinstance(s, str):
        return None
    s = s.strip()
    for fmt in _DATE_FMTS:
        try:
            return _dt.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _inner_fields(extracted_fields) -> dict:
    """`extracted_fields` is `{doc_type, fields, extracted_at}` — return the
    inner `fields` dict (falling back to the top level for older rows)."""
    ef = extracted_fields or {}
    inner = ef.get("fields")
    return inner if isinstance(inner, dict) else (ef if isinstance(ef, dict) else {})


def _find_deadline(fields: dict) -> tuple[_dt.date | None, str | None, str | None]:
    """Return (date, original_str, matched_key) for the first deadline-like
    field, scanning curated keys then universal `dates`/`key_facts` arrays."""
    for key in _DEADLINE_KEYS:
        v = fields.get(key)
        if isinstance(v, str) and v.strip():
            d = _parse_date(v)
            if d:
                return d, v.strip(), key
    for arr_name in ("dates", "key_facts"):
        for item in (fields.get(arr_name) or []):
            if not isinstance(item, dict):
                continue
            label = (item.get("label") or "").lower()
            if any(k in label for k in ("expir", "valid_until", "valid_thru", "end_date", "due")):
                d = _parse_date(item.get("value"))
                if d:
                    return d, item.get("value"), label
    return None, None, None


def alerts_for_document(doc, *, today: _dt.date | None = None) -> list[dict]:
    """Built-in attention rules for one ORM Document. Returns a list of alert
    dicts: {type, severity, title, detail, documentId, documentName, docType,
    dueAt, daysDelta}. Severity ∈ {high, warn, review}."""
    today = today or _dt.date.today()
    out: list[dict] = []
    base = {
        "documentId": doc.id_external,
        "documentName": doc.name,
        "docType": doc.doc_type,
    }

    # Ingestion failures always need attention.
    if (doc.ingestion_status or "") == "failed":
        out.append({**base, "type": "ingestion_failed", "severity": "high",
                    "title": "Ingestion failed", "detail": "This document could not be processed.",
                    "dueAt": None, "daysDelta": None})
        return out  # nothing else is meaningful for a failed doc

    if (doc.ingestion_status or "") != "ready":
        return out  # pending/processing — not actionable yet

    fields = _inner_fields(doc.extracted_fields)
    date, date_str, key = _find_deadline(fields)
    if date is not None:
        delta = (date - today).days
        is_payment = bool(key and _PAYMENT_RE.search(key))
        if delta < 0:
            out.append({**base,
                        "type": "overdue" if is_payment else "expired",
                        "severity": "high",
                        "title": "Overdue" if is_payment else "Expired",
                        "detail": (f"Payment was due {date_str} ({-delta} days ago)."
                                   if is_payment else
                                   f"Expired {date_str} ({-delta} days ago)."),
                        "dueAt": date.isoformat(), "daysDelta": delta})
        elif delta <= SOON_DAYS:
            out.append({**base,
                        "type": "due_soon" if is_payment else "expiring_soon",
                        "severity": "warn",
                        "title": "Due soon" if is_payment else "Expiring soon",
                        "detail": (f"Payment due {date_str} (in {delta} days)."
                                   if is_payment else
                                   f"Expires {date_str} (in {delta} days)."),
                        "dueAt": date.isoformat(), "daysDelta": delta})

    # Low-confidence / unclassified → flag for review.
    conf = doc.doc_type_confidence
    if not doc.doc_type or doc.doc_type == "unclassified":
        out.append({**base, "type": "unclassified", "severity": "review",
                    "title": "Needs review", "detail": "Could not be confidently classified.",
                    "dueAt": None, "daysDelta": None})
    elif conf is not None and float(conf) < LOW_CONFIDENCE:
        out.append({**base, "type": "low_confidence", "severity": "review",
                    "title": "Low-confidence classification",
                    "detail": f"Classified as “{doc.doc_type}” at {float(conf):.0%} — worth a check.",
                    "dueAt": None, "daysDelta": None})

    # G3 · low OCR confidence — scanned/photographed pages that may be garbled.
    oq = getattr(doc, "ocr_quality", None)
    if isinstance(oq, dict) and oq.get("flagged"):
        n = oq.get("lowConfidencePages") or 0
        out.append({**base, "type": "low_ocr_confidence", "severity": "review",
                    "title": "Low OCR confidence",
                    "detail": f"{n} scanned page(s) may be poorly read — re-upload a clearer scan or verify.",
                    "dueAt": None, "daysDelta": None})

    return out


_SEVERITY_ORDER = {"high": 0, "warn": 1, "review": 2}


def evaluate(docs, *, today: _dt.date | None = None) -> list[dict]:
    """Run the rules over an iterable of ORM Documents and return alerts sorted
    by severity, then soonest deadline."""
    today = today or _dt.date.today()
    alerts: list[dict] = []
    for d in docs:
        alerts.extend(alerts_for_document(d, today=today))
    alerts.sort(key=lambda a: (
        _SEVERITY_ORDER.get(a["severity"], 9),
        a["daysDelta"] if a["daysDelta"] is not None else 10**6,
    ))
    return alerts
