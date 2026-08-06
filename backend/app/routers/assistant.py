"""Document-intelligence personal assistant — the "watchlist".

Scans the signed-in user's documents for FORWARD-LOOKING actionable dates (expiry, renewal,
payment-due, contract-end, review) and turns them into urgency-ranked items with a plain-language
suggestion + a one-click calendar reminder (.ics with an alarm). Per-user scoped like everything else.

    GET  /api/assistant/watchlist          → the ranked action items
    GET  /api/assistant/event.ics?...      → an iCalendar reminder for one item (VALARM N days before)
"""
from __future__ import annotations

import datetime as _dt
import re

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.documents_scope import get_current_owner_user_pk
from app.orm import Document
from app.security import CurrentUser, require_role

router = APIRouter()

_ISO = re.compile(r"^(19|20)\d{2}-[01]?\d-[0-3]?\d$")
_DMY = re.compile(r"^([0-3]?\d)[-/]([01]?\d)[-/]((?:19|20)\d{2})$")


def _parse_date(s: str) -> _dt.date | None:
    s = (s or "").strip()[:10].replace("/", "-") if s else ""
    if not s:
        return None
    m = re.match(r"^((?:19|20)\d{2})-([01]?\d)-([0-3]?\d)$", s)
    if m:
        try:
            return _dt.date(int(m[1]), int(m[2]), int(m[3]))
        except ValueError:
            return None
    m = re.match(r"^([0-3]?\d)-([01]?\d)-((?:19|20)\d{2})$", s)
    if m:
        try:
            return _dt.date(int(m[3]), int(m[2]), int(m[1]))
        except ValueError:
            return None
    return None


# Human labels per doc-type for the item title.
_TYPE_LABEL = {
    "passport": "Passport", "national_id": "ID card", "visa": "Visa",
    "credit_card_statement": "Credit card", "bank_statement": "Bank statement",
    "invoice": "Invoice", "bill": "Bill", "master_service_agreement": "Contract",
    "contract": "Contract", "insurance_policy": "Insurance policy",
    "training_certificate": "Certification", "certificate": "Certification",
    "driver_licence": "Driver's licence", "residency_permit": "Residency permit",
}


def _classify(key: str, doc_type: str, doc_name: str) -> tuple[str, str, str] | None:
    """(field_key, doc_type) → (kind, title, suggestion) for FORWARD-LOOKING actions, else None.
    kind ∈ expiry | payment | renewal | contract | review."""
    k = (key or "").lower()
    t = (doc_type or "").lower()
    nm = (doc_name or "").lower()
    label = _TYPE_LABEL.get(t, "Document")

    # Never actionable — issuance, birth, statement windows, plain invoice date.
    if k in ("date_of_birth", "dob", "issue_date", "issued_on", "statement_period_start_date",
             "statement_period_end_date", "invoice_date", "primary_date", "print_date"):
        return None

    is_esta = "esta" in nm or "authorization" in nm or "authorisation" in nm

    if any(w in k for w in ("expir", "valid_until", "valid_to", "expires")):
        if t in ("passport", "national_id", "driver_licence", "residency_permit") or is_esta:
            what = "Travel authorization (ESTA)" if is_esta else label
            return ("expiry", f"{what} expires",
                    "Many countries require 6 months' validity to travel — renew in good time if you have trips planned.")
        if t in ("training_certificate", "certificate"):
            return ("expiry", "Certification expires",
                    "Renew or re-certify before it lapses to keep it valid.")
        if t in ("insurance_policy",):
            return ("renewal", "Insurance renews",
                    "Review coverage and compare quotes before it auto-renews.")
        if t in ("master_service_agreement", "contract"):
            return ("contract", "Contract expires",
                    "Check the notice period — it may auto-renew unless you give notice.")
        return ("expiry", f"{label} expires", "Review whether this needs renewing.")

    if "expiration_date" in k or (k in ("end_date", "contract_end_date") and t in ("master_service_agreement", "contract")):
        return ("contract", "Contract expires",
                "Check the notice period — it may auto-renew unless you give notice.")

    if any(w in k for w in ("payment_due", "due_date", "min_payment_due_date")):
        if t == "credit_card_statement":
            return ("payment", "Credit-card payment due",
                    "Pay at least the minimum by this date to avoid interest and late fees.")
        if t in ("invoice", "bill"):
            return ("payment", "Invoice payment due", "Settle by this date to stay on terms.")
        return ("payment", "Payment due", "A payment is due by this date.")

    if any(w in k for w in ("renew", "next_renewal", "renewal_date")):
        return ("renewal", f"{label} renews", "Review before it renews.")

    if "maturity" in k:
        return ("review", "Matures", "This instrument matures on this date.")

    if k in ("review_date", "next_review"):
        return ("review", "Review due", "A scheduled review falls on this date.")

    return None


def _urgency(days: int, kind: str) -> str:
    if days < 0:
        return "overdue"
    if days <= 7:
        return "urgent"
    if days <= 30:
        return "soon"
    if days <= 90:
        return "upcoming"
    return "info"


def _derive_items(db: Session) -> list[dict]:
    owner = get_current_owner_user_pk()
    if owner is None:
        return []  # fail closed — never enumerate every tenant's documents without an owner scope
    q = select(Document).where(Document.ingestion_status == "ready", Document.owner_user_id == owner)
    docs = db.scalars(q).all()
    today = _dt.date.today()
    items: list[dict] = []
    for d in docs:
        fields = ((d.extracted_fields or {}).get("fields") or {})
        for k, v in fields.items():
            if not isinstance(v, (str, int)):
                continue
            dt = _parse_date(str(v))
            if not dt:
                continue
            cls = _classify(k, d.doc_type or "", d.name or "")
            if not cls:
                continue
            kind, title, suggestion = cls
            days = (dt - today).days
            # Drop stale one-offs (old payment already long past). Keep future + recently overdue.
            if days < -45:
                continue
            items.append({
                "docId": d.id_external, "docName": d.name, "docType": d.doc_type,
                "field": k, "kind": kind, "title": title,
                "date": dt.isoformat(), "daysUntil": days,
                "urgency": _urgency(days, kind), "suggestion": suggestion,
                "icsUrl": f"/api/assistant/event.ics?doc_id={d.id_external}&field={k}",
            })
    # Nearest first; overdue floats to the very top.
    items.sort(key=lambda x: (x["daysUntil"] < 0 and -1 or 0, x["date"]))
    return items


@router.get("/assistant/watchlist")
def watchlist(
    db: Session = Depends(get_session),
    _user: CurrentUser = Depends(require_role("admin", "reviewer")),
) -> dict:
    # P2 · cloud-only premium feature — OSS returns empty.
    from app.license import is_cloud
    if not is_cloud():
        return {"items": [], "counts": {"overdue": 0, "urgent": 0, "soon": 0, "upcoming": 0, "info": 0}, "total": 0}
    items = _derive_items(db)
    buckets = {"overdue": [], "urgent": [], "soon": [], "upcoming": [], "info": []}
    for it in items:
        buckets[it["urgency"]].append(it)
    return {"items": items, "counts": {k: len(v) for k, v in buckets.items()}, "total": len(items)}


def _ics_escape(s: str) -> str:
    # Escape CR as well as LF — a bare \r in a doc name would otherwise break the
    # folded iCal line (RFC 5545 line structure / injection).
    return ((s or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
            .replace("\r\n", "\\n").replace("\r", "\\n").replace("\n", "\\n"))


@router.get("/assistant/event.ics")
def event_ics(
    doc_id: str = Query(...),
    field: str = Query(...),
    remind_days: int = Query(default=14, ge=0, le=120),
    db: Session = Depends(get_session),
    _user: CurrentUser = Depends(require_role("admin", "reviewer")),
) -> Response:
    """A one-event iCalendar reminder with an alarm `remind_days` before. Import into Google/Apple/
    Outlook calendar → a real reminder that persists, no push infrastructure needed."""
    owner = get_current_owner_user_pk()
    q = select(Document).where(Document.id_external == doc_id)
    if owner is not None:
        q = q.where(Document.owner_user_id == owner)
    doc = db.scalar(q)
    if doc is None:
        return Response(status_code=404, content="not found")
    val = ((doc.extracted_fields or {}).get("fields") or {}).get(field)
    dt = _parse_date(str(val)) if val is not None else None
    cls = _classify(field, doc.doc_type or "", doc.name or "")
    if dt is None or cls is None:
        return Response(status_code=422, content="no actionable date on that field")
    kind, title, suggestion = cls
    summary = f"{title} — {doc.name}"
    ymd = dt.strftime("%Y%m%d")
    uid = f"{doc.id_external}-{field}@docaiq"
    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//DocAIQ//Assistant//EN",
        "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
        "BEGIN:VEVENT", f"UID:{uid}",
        f"DTSTART;VALUE=DATE:{ymd}", f"DTEND;VALUE=DATE:{ymd}",
        f"SUMMARY:{_ics_escape(summary)}",
        f"DESCRIPTION:{_ics_escape(suggestion + '  · From DocAIQ · ' + (doc.name or ''))}",
        "TRANSP:TRANSPARENT",
        "BEGIN:VALARM", f"TRIGGER:-P{remind_days}D", "ACTION:DISPLAY",
        f"DESCRIPTION:{_ics_escape(summary)}", "END:VALARM",
        "END:VEVENT", "END:VCALENDAR",
    ]
    body = "\r\n".join(lines) + "\r\n"
    fn = re.sub(r"[^a-zA-Z0-9]+", "_", (title or "reminder")).strip("_").lower()
    return Response(content=body, media_type="text/calendar",
                    headers={"Content-Disposition": f'attachment; filename="{fn}.ics"'})
