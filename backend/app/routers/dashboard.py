"""Dashboard — user-customizable infographic dashboard.

CRUD endpoints for the user's dashboard config (widget layout) + widget
preview (compute a single widget's data without saving) + AI widget proposals.

    GET    /dashboard/config          → user's saved widget config (auto-generate if none)
    PUT    /dashboard/config          → save widget layout
    POST   /dashboard/widgets/preview → preview one widget's computed data
    POST   /dashboard/widgets/propose → AI-proposed widgets (values-free corpus profile)

Owner-scoped. The heavy data computation lives in services/dashboard_data.py.
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_current_tenant, get_session
from app.documents_scope import get_current_owner_user_pk
from app.orm import DashboardConfig, Document
from app.security import CurrentUser, get_current_user

router = APIRouter()


def _guard() -> None:
    if get_settings().product != "documents":
        raise HTTPException(status_code=404, detail="Not available")


def _owner_docs(db: Session, tid: str, uid: int) -> list[Document]:
    return list(db.scalars(select(Document).where(
        Document.tenant_id == tid,
        Document.owner_user_id == uid,
        Document.is_archived.is_(False),
        Document.ingestion_status == "ready",
    )).all())


# ── Built-in widget templates ────────────────────────────────────────────────

BUILTIN_WIDGETS: list[dict] = [
    # Finance
    {"kind": "kpi", "module": "finance", "title": "Portfolio Value",
     "config": {"docTypes": ["bank_statement", "brokerage_statement", "investment_statement"],
                "metric": "portfolio_value"}, "size": "small"},
    {"kind": "donut", "module": "finance", "title": "Asset Allocation",
     "config": {"docTypes": ["bank_statement", "brokerage_statement", "investment_statement"],
                "field": "asset_class", "metric": "value"}, "size": "medium"},
    {"kind": "trend", "module": "finance", "title": "Monthly Cash Flow",
     "config": {"docTypes": ["bank_statement", "brokerage_statement"],
                "field": "cash_flow", "metric": "net"}, "size": "large"},
    {"kind": "table", "module": "finance", "title": "Recent Transactions",
     "config": {"docTypes": ["bank_statement", "credit_card_statement"],
                "field": "transactions"}, "size": "large"},
    # Expense
    {"kind": "kpi", "module": "expense", "title": "Total Spend",
     "config": {"docTypes": ["receipt", "invoice", "credit_card_statement", "bill"],
                "metric": "total_spend"}, "size": "small"},
    {"kind": "donut", "module": "expense", "title": "Spend by Category",
     "config": {"docTypes": ["receipt", "invoice", "credit_card_statement", "bill"],
                "field": "category", "metric": "total_amount"}, "size": "medium"},
    {"kind": "bars", "module": "expense", "title": "Top Merchants",
     "config": {"docTypes": ["receipt", "invoice", "credit_card_statement", "bill"],
                "field": "merchant", "metric": "total_amount"}, "size": "medium"},
    {"kind": "trend", "module": "expense", "title": "Monthly Spend",
     "config": {"docTypes": ["receipt", "invoice", "credit_card_statement", "bill"],
                "metric": "monthly_spend"}, "size": "large"},
    # Accounting
    {"kind": "kpi", "module": "accounting", "title": "Revenue",
     "config": {"docTypes": ["income_statement", "profit_and_loss", "balance_sheet"],
                "metric": "revenue"}, "size": "small"},
    {"kind": "kpi", "module": "accounting", "title": "Net Income",
     "config": {"docTypes": ["income_statement", "profit_and_loss"],
                "metric": "net_income"}, "size": "small"},
    {"kind": "bars", "module": "accounting", "title": "P&L Summary",
     "config": {"docTypes": ["income_statement", "profit_and_loss"],
                "field": "line_item", "metric": "amount"}, "size": "large"},
    {"kind": "comparison", "module": "accounting", "title": "Assets = Liabilities + Equity",
     "config": {"docTypes": ["balance_sheet"],
                "metric": "balance_check"}, "size": "medium"},
    {"kind": "table", "module": "accounting", "title": "Accounts Payable",
     "config": {"docTypes": ["invoice", "bill"], "field": "payables"}, "size": "large"},
    # Identity
    {"kind": "kpi", "module": "identity", "title": "Identity Documents",
     "config": {"docTypes": ["passport", "national_id", "driver_license", "visa", "aadhaar_card"],
                "metric": "doc_count"}, "size": "small"},
    {"kind": "feed", "module": "identity", "title": "Upcoming Expiries",
     "config": {"docTypes": ["passport", "national_id", "driver_license", "visa",
                              "residency_permit", "insurance_policy"],
                "metric": "expiry_feed"}, "size": "medium"},
    {"kind": "donut", "module": "identity", "title": "Documents by Type",
     "config": {"docTypes": ["passport", "national_id", "driver_license", "visa",
                              "aadhaar_card", "residency_permit"],
                "field": "doc_type", "metric": "count"}, "size": "medium"},
    # Health
    {"kind": "kpi", "module": "health", "title": "Lab Reports",
     "config": {"docTypes": ["lab_report", "blood_test", "pathology_report",
                              "medical_report", "health_checkup"],
                "metric": "report_count"}, "size": "small"},
    {"kind": "kpi", "module": "health", "title": "Out of Range Markers",
     "config": {"docTypes": ["lab_report", "blood_test", "pathology_report",
                              "medical_report", "health_checkup"],
                "metric": "out_of_range"}, "size": "small"},
    {"kind": "heatmap", "module": "health", "title": "Lab Results Trend",
     "config": {"docTypes": ["lab_report", "blood_test", "pathology_report",
                              "medical_report", "health_checkup"],
                "metric": "trend_matrix"}, "size": "full"},
]


# ── Auto-detect: which built-in widgets are relevant for the user's docs ─────


def _auto_detect_widgets(docs: list[Document]) -> list[dict]:
    """Return the subset of built-in widgets whose docTypes intersect the user's
    actual document types."""
    present_types = {d.doc_type for d in docs if d.doc_type}
    widgets: list[dict] = []
    seen_modules: set[str] = set()
    for w in BUILTIN_WIDGETS:
        needed = set(w["config"].get("docTypes") or [])
        if needed & present_types:
            widgets.append({**w, "id": f"auto_{w['module']}_{w['kind']}_{len(widgets)}",
                           "source": "builtin", "pinned": True})
            seen_modules.add(w["module"])
    # Sort by module grouping
    MODULE_ORDER = {"finance": 0, "expense": 1, "accounting": 2, "identity": 3, "health": 4}
    widgets.sort(key=lambda w: (MODULE_ORDER.get(w["module"], 9), w.get("position", 0)))
    return widgets


# ── Endpoints ────────────────────────────────────────────────────────────────


@router.get("/dashboard/config")
def get_config(
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(get_current_user),
) -> dict:
    """Return the user's saved dashboard config. Auto-generate from built-in
    templates on first visit (no saved row)."""
    _guard()
    tid = get_current_tenant()
    uid = get_current_owner_user_pk()

    row = db.scalar(select(DashboardConfig).where(
        DashboardConfig.tenant_id == tid,
        DashboardConfig.owner_user_id == uid,
    ))

    if row is not None:
        return {"widgets": row.config if isinstance(row.config, list) else [],
                "autoGenerated": False}

    # First visit — auto-detect relevant widgets
    docs = _owner_docs(db, tid, uid)
    widgets = _auto_detect_widgets(docs)
    return {"widgets": widgets, "autoGenerated": True}


@router.put("/dashboard/config")
def save_config(
    body: dict = Body(...),
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(get_current_user),
) -> dict:
    """Save the user's dashboard widget layout."""
    _guard()
    tid = get_current_tenant()
    uid = get_current_owner_user_pk()
    widgets = body.get("widgets") if isinstance(body.get("widgets"), list) else []

    row = db.scalar(select(DashboardConfig).where(
        DashboardConfig.tenant_id == tid,
        DashboardConfig.owner_user_id == uid,
    ))

    if row is None:
        row = DashboardConfig(tenant_id=tid, owner_user_id=uid, config=widgets)
        db.add(row)
    else:
        row.config = widgets
    db.commit()
    return {"widgets": widgets, "saved": True}


@router.post("/dashboard/widgets/preview")
def preview_widget(
    body: dict = Body(...),
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(get_current_user),
) -> dict:
    """Compute a single widget's data without saving it. Useful for the
    "Add widget" flow — user can preview before committing."""
    _guard()
    tid = get_current_tenant()
    uid = get_current_owner_user_pk()
    docs = _owner_docs(db, tid, uid)
    from app.services.dashboard_data import compute_widget
    result = compute_widget(body, docs, db)
    return {"widget": body, "data": result}


@router.post("/dashboard/widgets/propose")
def propose_widgets(
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(get_current_user),
) -> dict:
    """AI-proposed widgets based on the user's document corpus. Uses the same
    values-free pattern as intelligence/proposals.py — only field names and
    doc-type counts are sent to the LLM, never field values."""
    _guard()
    tid = get_current_tenant()
    uid = get_current_owner_user_pk()
    docs = _owner_docs(db, tid, uid)

    if not docs:
        return {"proposed": [], "message": "Upload some documents first."}

    # Build values-free corpus profile
    from collections import Counter
    type_counts = Counter(d.doc_type for d in docs if d.doc_type)
    field_names: set[str] = set()
    for d in docs:
        ef = d.extracted_fields or {}
        inner = ef.get("fields") if isinstance(ef.get("fields"), dict) else ef
        if isinstance(inner, dict):
            field_names.update(k for k in inner if isinstance(k, str))

    profile = {
        "docTypeCounts": dict(type_counts.most_common(30)),
        "fieldNames": sorted(field_names)[:80],
        "totalDocs": len(docs),
    }

    # Call LLM (Haiku — cheap, fast) to suggest widgets
    try:
        from app.config import get_settings
        from app.llm import gateway
        model = get_settings().intelligence_model
        system = _build_proposal_prompt(profile)
        result = gateway.call(
            model,
            [gateway.Message(role="user", content=system)],
            temperature=0.2, max_tokens=1200, structured=False,
            tenant_id=tid, task_kind="dashboard_propose",
        )
        raw = result.text if result.text else ""
        proposed = _sanitize_proposals(raw, profile, docs)
        return {"proposed": proposed, "profileTypes": list(type_counts.keys())}
    except Exception:
        return {"proposed": [], "message": "AI proposal unavailable right now.",
                "profileTypes": list(type_counts.keys())}


def _build_proposal_prompt(profile: dict) -> str:
    import json
    available_modules = ["finance", "expense", "accounting", "identity", "health"]
    available_kinds = ["kpi", "donut", "bars", "trend", "table", "feed", "comparison", "heatmap"]
    return f"""You are designing an infographic dashboard for a user who uploaded documents.

Available document types and counts:
{json.dumps(profile['docTypeCounts'], indent=2)}

Field names available across their documents (values NOT included — privacy):
{json.dumps(profile['fieldNames'], indent=2)}

Propose 2-4 dashboard widgets. Each widget is a self-contained visualization.
Available kinds: {', '.join(available_kinds)}
Available modules: {', '.join(available_modules)}

Return a JSON array of widget objects:
[{{"kind": "kpi", "module": "finance", "title": "Portfolio Value",
   "config": {{"docTypes": ["bank_statement"], "metric": "portfolio_value"}},
   "size": "small", "rationale": "..."}}]

RULES:
- Only use docTypes from the list above.
- Only reference field names from the list above.
- NEVER include actual values or PII.
- "size" ∈ small|medium|large|full.
- 2-4 widgets max.
- Return ONLY the JSON array, no markdown."""


def _sanitize_proposals(raw: str, profile: dict, docs) -> list[dict]:
    import json
    import re
    # Extract JSON array from possible markdown wrapper
    m = re.search(r"\[.*\]", raw, re.DOTALL)
    if not m:
        return []
    try:
        parsed = json.loads(m[0])
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []

    valid_kinds = {"kpi", "donut", "bars", "trend", "table", "feed", "comparison", "heatmap"}
    valid_modules = {"finance", "expense", "accounting", "identity", "health"}
    valid_types = set(profile.get("docTypeCounts") or {})
    valid_sizes = {"small", "medium", "large", "full"}

    out = []
    for i, w in enumerate(parsed):
        if not isinstance(w, dict):
            continue
        kind = w.get("kind", "kpi")
        if kind not in valid_kinds:
            kind = "kpi"
        module = w.get("module", "finance")
        if module not in valid_modules:
            module = "finance"
        config = w.get("config", {}) if isinstance(w.get("config"), dict) else {}
        doc_types = [t for t in (config.get("docTypes") or []) if t in valid_types]
        size = w.get("size", "medium")
        if size not in valid_sizes:
            size = "medium"

        out.append({
            "id": f"ai_{module}_{kind}_{i}",
            "kind": kind, "module": module,
            "title": str(w.get("title", f"{module.title()} {kind.title()}"))[:60],
            "config": {**config, "docTypes": doc_types},
            "size": size, "source": "ai", "pinned": False,
            "rationale": str(w.get("rationale", ""))[:200],
        })
    return out
