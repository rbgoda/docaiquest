"""On-demand analytics dashboards — document-type-driven, owner-scoped.

Two endpoints power the Analytics tab (a dashboard *builder*):

  GET  /api/analytics/dashboards          → themes available to THIS user +, for
                                            each, the matching documents (so the
                                            UI can show a tick-to-include checklist).
  POST /api/analytics/dashboards/{theme}  → {docIds:[...]} → the aggregated
                                            dashboard payload for the selected docs.

Everything is computed over the current owner's own ready documents (per-user
isolation via the owner ContextVar) and is deterministic (no LLM). Theme
definitions + aggregation live in `app.analytics_themes`.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import analytics_themes as themes
from app.config import get_settings
from app.db import get_current_tenant, get_session
from app.documents_scope import get_current_owner_user_pk
from app.orm import Document
from app.security import CurrentUser, get_current_user

router = APIRouter()


def _guard() -> None:
    if get_settings().product != "documents":
        raise HTTPException(status_code=404, detail="Not available")


def _owner_ready_docs(db: Session) -> list[Document]:
    tid = get_current_tenant()
    uid = get_current_owner_user_pk()
    return list(db.scalars(
        select(Document).where(
            Document.tenant_id == tid,
            Document.owner_user_id == uid,
            Document.is_archived.is_(False),
            Document.ingestion_status == "ready",
        ).order_by(Document.pk.desc())
    ).all())


def _doc_brief(d: Document) -> dict:
    return {
        "id": d.id_external,
        "name": d.name,
        "docType": d.doc_type or "unclassified",
        "date": d.created_at.isoformat() if d.created_at else None,
    }


@router.get("/analytics/dashboards")
def list_dashboards(db: Session = Depends(get_session),
                    user: CurrentUser = Depends(get_current_user)) -> dict:
    """Themes this user can build right now, each with its matching documents for
    the include-checklist. Themes with zero matching docs are still listed (so the
    user sees what unlocks them) but flagged `available=false`."""
    _guard()
    docs = _owner_ready_docs(db)
    matches: dict[str, list[dict]] = {k: [] for k in themes.THEMES}
    for d in docs:
        for key in themes.theme_for_type(d.doc_type):
            matches[key].append(_doc_brief(d))

    out = []
    for key, meta in themes.THEMES.items():
        docs_for = matches[key]
        out.append({
            "key": key,
            "label": meta["label"],
            "description": meta["description"],
            "icon": meta["icon"],
            "available": bool(docs_for),
            "docCount": len(docs_for),
            "documents": docs_for,
        })
    # available themes first, then by doc count
    out.sort(key=lambda t: (not t["available"], -t["docCount"]))
    return {"themes": out, "totalDocs": len(docs)}


class BuildPayload(BaseModel):
    docIds: list[str] = Field(default_factory=list, max_length=500)
    months: int = Field(default=0, ge=0, le=120)  # 0 = all time


def _build(theme: str, payload: BuildPayload, db: Session) -> dict:
    """Shared: aggregate the owner's selected docs of this theme into a payload.
    Stray/foreign ids are ignored — never cross-tenant."""
    meta = themes.THEMES.get(theme)
    if meta is None:
        raise HTTPException(status_code=404, detail=f"Unknown dashboard theme '{theme}'")
    selected = set(payload.docIds or [])
    docs = [d for d in _owner_ready_docs(db)
            if theme in themes.theme_for_type(d.doc_type)
            and (not selected or d.id_external in selected)]
    if not docs:
        return {"theme": theme, "label": meta["label"], "icon": meta["icon"], "currency": None,
                "docCount": 0, "months": payload.months, "metrics": [], "sections": [],
                "empty": "No matching documents selected."}

    def _categorize(txns: list[dict]) -> None:
        from app.agents import categorizer
        categorizer.categorize_transactions(db, get_current_tenant(), txns, mode="expense")

    since = None
    if payload.months:
        from datetime import date as _date, timedelta as _td
        since = _date.today() - _td(days=payload.months * 31)

    built = meta["builder"](docs, categorize=_categorize, since=since)
    return {
        "theme": theme, "label": meta["label"], "icon": meta["icon"],
        "docCount": len(docs), "months": payload.months,
        "currency": built.get("currency"),
        "metrics": built.get("metrics", []), "sections": built.get("sections", []),
    }


@router.post("/analytics/dashboards/{theme}")
def build_dashboard(theme: str, payload: BuildPayload,
                    db: Session = Depends(get_session),
                    user: CurrentUser = Depends(get_current_user)) -> dict:
    """Aggregate the selected documents into the theme's dashboard."""
    _guard()
    return _build(theme, payload, db)


def _summarize_for_llm(built: dict) -> str:
    lines = [f"Dashboard: {built.get('label')}",
             f"Documents: {built.get('docCount')}"
             + (f" over the last {built['months']} months" if built.get("months") else " (all time)")
             + (f", currency {built['currency']}" if built.get("currency") else "")]
    for m in built.get("metrics", []):
        lines.append(f"- {m['label']}: {m['value']} {m.get('unit') or ''}".rstrip()
                     + (f" ({m['sub']})" if m.get("sub") else ""))
    for s in built.get("sections", []):
        if s["kind"] in ("donut", "bars") and s.get("items"):
            lines.append(f"{s['title']}: " + "; ".join(f"{i['label']}={i['value']}" for i in s["items"][:8]))
        elif s["kind"] == "trend" and s.get("points"):
            lines.append(f"{s['title']}: " + ", ".join(f"{p['label']}={p['value']}" for p in s["points"]))
        elif s["kind"] == "table" and s.get("rows"):
            lines.append(f"{s['title']}: {len(s['rows'])} rows, columns {s.get('columns')}")
    return "\n".join(lines)


@router.post("/analytics/dashboards/{theme}/insights")
def dashboard_insights(theme: str, payload: BuildPayload,
                       db: Session = Depends(get_session),
                       user: CurrentUser = Depends(get_current_user)) -> dict:
    """AI analysis of a built dashboard — observations + suggestions from the
    LLM (DashScope). Owner-scoped; the dashboard data (aggregates only) is what's
    sent, redacted at the gateway boundary."""
    _guard()
    built = _build(theme, payload, db)
    if built.get("empty") or not built.get("metrics"):
        return {"insights": [], "suggestions": [], "empty": "Nothing to analyze yet."}

    import json as _json
    from app.llm import gateway
    from app.config import get_settings
    model = get_settings().documents_categorize_model or "dashscope/qwen-max"
    system = (
        "You are a sharp, concrete analyst reviewing a personal document-analytics dashboard. "
        "From the figures, surface what genuinely stands out and what the person could do next. "
        "Be specific and quantitative — cite the actual numbers/percentages. Keep each item to one or "
        "two sentences. Do NOT give regulated investment/tax/legal advice; frame actions as 'consider' "
        "or 'you may want to'. Output STRICT JSON only."
    )
    user_msg = (
        f"{_summarize_for_llm(built)}\n\n"
        "Return JSON exactly like:\n"
        '{"insights":[{"tone":"positive|watch|risk|info","title":"short label","detail":"one sentence"}],'
        '"suggestions":[{"title":"short label","detail":"one actionable sentence"}]}\n'
        "Give 3-4 insights and 2-3 suggestions. tone reflects sentiment (positive good, watch caution, "
        "risk concerning, info neutral)."
    )
    try:
        result = gateway.call(
            model=model,
            messages=[gateway.Message(role="system", content=system),
                      gateway.Message(role="user", content=user_msg)],
            temperature=0.3, max_tokens=900, structured=True,
            tenant_id=get_current_tenant(), task_kind="analytics_insights",
        )
        text = (result.text or "{}").strip()
        try:
            data = _json.loads(text)
        except _json.JSONDecodeError:
            from json_repair import repair_json
            data = _json.loads(repair_json(text))
        ins = [i for i in (data.get("insights") or []) if isinstance(i, dict) and i.get("title")][:5]
        sug = [s for s in (data.get("suggestions") or []) if isinstance(s, dict) and s.get("title")][:4]
        for i in ins:
            if i.get("tone") not in ("positive", "watch", "risk", "info"):
                i["tone"] = "info"
        return {"insights": ins, "suggestions": sug, "model": result.provider}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"AI analysis unavailable right now ({e}).")
