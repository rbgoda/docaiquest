"""Intelligence Dashboard · Phase B — the view engine.

A "view" (use case) is a declarative spec — a doc-type filter + columns +
which metrics to show — evaluated over the user's `extracted_fields`. One
engine evaluates any spec; built-in specs ship here, AI-proposed specs land in
Phase C. Pure compute, no LLM. See docs/architecture/INTELLIGENCE_DASHBOARD.md.

Field access copes with the universal-extractor shape: values live either as
flat keys (`title`, `issuer`, `primary_date`, `primary_amount`) or inside
labeled arrays (`key_facts`/`identifiers`/`dates`/`amounts`: `[{label,value}]`).
`field_value()` looks in both. Deadlines reuse alerts._find_deadline so a view's
"Expiry" column and the alert engine never disagree.
"""
from __future__ import annotations

import datetime as _dt

from app.intelligence import alerts as _alerts

# Arrays of {label, value} the universal extractor emits.
_LABELED_ARRAYS = ("key_facts", "identifiers", "dates", "amounts", "parties")


def field_value(fields: dict, *names: str) -> str | None:
    """First non-empty value for any of `names`, checked as a flat key then as
    a label (case-insensitive substring) in the labeled arrays."""
    for n in names:
        v = fields.get(n)
        if isinstance(v, (str, int, float)) and str(v).strip():
            return str(v)
    lowered = [n.lower() for n in names]
    for arr in _LABELED_ARRAYS:
        for it in (fields.get(arr) or []):
            if not isinstance(it, dict):
                continue
            lab = (it.get("label") or "").lower()
            if lab and any(n in lab or lab in n for n in lowered):
                val = it.get("value")
                if val not in (None, ""):
                    return str(val)
    return None


# ---- Built-in views --------------------------------------------------------
# Each column: {"label", "get": [field names]} or {"label", "deadline": True}.
# metrics: list of {"label", "kind"} where kind ∈ count|past|soon.
BUILTIN_VIEWS: list[dict] = [
    {
        "id": "ap", "title": "Accounts Payable", "icon": "💸",
        "subtitle": "Invoices and what's owed",
        "docTypes": ["invoice", "bill", "receipt"],
        "columns": [
            {"label": "From", "get": ["issuer", "vendor", "title"]},
            {"label": "Reference", "get": ["invoice_number", "invoice_no", "reference", "identifier"]},
            {"label": "Amount", "get": ["primary_amount", "total_due", "total", "amount_due", "amount"]},
            {"label": "Date", "get": ["primary_date", "invoice_date", "date"]},
        ],
        "metrics": [{"label": "Invoices", "kind": "count"}, {"label": "Overdue", "kind": "past"}],
        "sort": "deadline",
    },
    {
        "id": "ids", "title": "IDs & expiry tracker", "icon": "🪪",
        "subtitle": "Identity & validity documents by expiry",
        "docTypes": ["passport", "id_card", "identity_card", "national_id", "drivers_license",
                     "license", "certificate", "training_certificate", "travel_authorization",
                     "visa", "insurance", "insurance_policy", "permit"],
        "columns": [
            {"label": "Holder / subject", "get": ["subject_or_recipient", "name", "holder", "title"]},
            {"label": "Type", "get": ["__doctype__"]},
            {"label": "Expires", "deadline": True},
        ],
        "metrics": [{"label": "Documents", "kind": "count"},
                    {"label": "Expiring ≤30d", "kind": "soon"}, {"label": "Expired", "kind": "past"}],
        "sort": "deadline",
    },
    {
        "id": "agreements", "title": "Agreements & policies", "icon": "📜",
        "subtitle": "Contracts, policies and their key dates",
        "docTypes": ["contract", "agreement", "policy", "policy_or_procedure", "terms", "sow", "nda"],
        "columns": [
            {"label": "Title", "get": ["title", "subject_or_recipient"]},
            {"label": "Party", "get": ["issuer", "counterparty", "party", "vendor"]},
            {"label": "Key date", "deadline": True},
        ],
        "metrics": [{"label": "Documents", "kind": "count"}, {"label": "Expiring ≤30d", "kind": "soon"}],
        "sort": "deadline",
    },
]


def _matches(doc, spec) -> bool:
    dt = (doc.doc_type or "").lower()
    return dt in spec["docTypes"]


def _cell(fields, col, doc, deadline):
    if col.get("deadline"):
        return deadline.isoformat() if deadline else None
    gets = col.get("get") or []
    if gets == ["__doctype__"]:
        return doc.doc_type
    return field_value(fields, *gets)


def evaluate_view(docs, spec, *, today: _dt.date | None = None) -> dict | None:
    """Evaluate one view spec over the given ORM docs. Returns None if no doc
    matches (so empty views don't clutter the dashboard)."""
    today = today or _dt.date.today()
    matched = [d for d in docs if _matches(d, spec) and (d.ingestion_status or "") == "ready"]
    if not matched:
        return None

    rows, n_past, n_soon = [], 0, 0
    for d in matched:
        fields = _alerts._inner_fields(d.extracted_fields)
        deadline, _, _ = _alerts._find_deadline(fields)
        delta = (deadline - today).days if deadline else None
        if delta is not None:
            if delta < 0:
                n_past += 1
            elif delta <= _alerts.SOON_DAYS:
                n_soon += 1
        rows.append({
            "documentId": d.id_external,
            "documentName": d.name,
            "cells": [_cell(fields, c, d, deadline) for c in spec["columns"]],
            "deadline": deadline.isoformat() if deadline else None,
            "daysDelta": delta,
            "flag": ("past" if (delta is not None and delta < 0)
                     else "soon" if (delta is not None and delta <= _alerts.SOON_DAYS) else None),
        })

    # Sort by soonest deadline (docs without one sink to the bottom).
    if spec.get("sort") == "deadline":
        rows.sort(key=lambda r: (r["daysDelta"] if r["daysDelta"] is not None else 10**6))

    kind_val = {"count": len(matched), "past": n_past, "soon": n_soon}
    metrics = [{"label": m["label"], "value": kind_val.get(m["kind"], 0)} for m in spec["metrics"]]

    return {
        "id": spec["id"], "title": spec["title"], "icon": spec["icon"],
        "subtitle": spec.get("subtitle"),
        "columns": [c["label"] for c in spec["columns"]],
        "metrics": metrics,
        "matchedCount": len(matched),
        "rows": rows,
    }


def evaluate_all(docs, *, today: _dt.date | None = None) -> list[dict]:
    """Evaluate every built-in view; return only those that matched."""
    out = []
    for spec in BUILTIN_VIEWS:
        res = evaluate_view(docs, spec, today=today)
        if res:
            out.append(res)
    return out
