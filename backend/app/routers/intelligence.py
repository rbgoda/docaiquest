"""Intelligence Dashboard · Phase A endpoints.

Documents-product only (404 elsewhere), owner-scoped. Phase A ships the
portfolio header + the zero-LLM alert engine; the view-engine + AI proposal
(Phases B/C) land later. See docs/architecture/INTELLIGENCE_DASHBOARD.md.
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_current_tenant, get_session
from app.documents_scope import get_current_owner_user_pk
from app.intelligence import alerts as alert_engine
from app.intelligence import proposals, view_engine
from app.orm import Document, SavedView
from app.security import CurrentUser, get_current_user

router = APIRouter()


def _guard() -> None:
    if get_settings().product != "documents":
        raise HTTPException(status_code=404, detail="Not available")


@router.get("/intelligence/overview")
def overview(db: Session = Depends(get_session),
             user: CurrentUser = Depends(get_current_user)) -> dict:
    """Portfolio header + attention alerts over the caller's own documents."""
    _guard()
    tid = get_current_tenant()
    uid = get_current_owner_user_pk()

    docs = list(db.scalars(
        select(Document).where(
            Document.tenant_id == tid,
            Document.owner_user_id == uid,
            Document.is_archived.is_(False),
        ).order_by(Document.pk.desc())
    ).all())

    by_type: dict[str, int] = {}
    ready = 0
    for d in docs:
        if d.ingestion_status == "ready":
            ready += 1
            t = d.doc_type or "unclassified"
            by_type[t] = by_type.get(t, 0) + 1

    alerts = alert_engine.evaluate(docs)
    by_sev: dict[str, int] = {}
    for a in alerts:
        by_sev[a["severity"]] = by_sev.get(a["severity"], 0) + 1

    # "Needs attention" = distinct documents with at least one alert.
    needs_attention = len({a["documentId"] for a in alerts})

    return {
        "portfolio": {
            "totalDocs": len(docs),
            "readyDocs": ready,
            "typeCount": len(by_type),
            "needsAttention": needs_attention,
            "byType": [{"type": k, "count": v}
                       for k, v in sorted(by_type.items(), key=lambda kv: -kv[1])],
        },
        "alertCounts": by_sev,
        "alerts": alerts,
    }


def _owned_docs(db: Session, tid: str, uid: int):
    return list(db.scalars(select(Document).where(
        Document.tenant_id == tid, Document.owner_user_id == uid,
        Document.is_archived.is_(False))).all())


@router.get("/intelligence/views")
def views(db: Session = Depends(get_session),
          user: CurrentUser = Depends(get_current_user)) -> dict:
    """Built-in views (Phase B) + the caller's saved/AI views (Phase C),
    evaluated over their own documents. Dismissed views are excluded; only
    views matching ≥1 document are returned."""
    _guard()
    tid = get_current_tenant()
    uid = get_current_owner_user_pk()
    docs = _owned_docs(db, tid, uid)

    out = [{**v, "source": "builtin"} for v in view_engine.evaluate_all(docs)]
    saved = db.scalars(select(SavedView).where(
        SavedView.tenant_id == tid, SavedView.owner_user_id == uid,
        SavedView.dismissed.is_(False))).all()
    for sv in saved:
        res = view_engine.evaluate_view(docs, sv.spec)
        if res:
            out.append({**res, "source": sv.source, "pinned": sv.pinned})
    # Pinned first, then AI/user, then built-ins.
    out.sort(key=lambda v: (not v.get("pinned", False), v.get("source") == "builtin"))
    return {"views": out}


@router.post("/intelligence/propose")
def propose(db: Session = Depends(get_session),
            user: CurrentUser = Depends(get_current_user)) -> dict:
    """Phase C — ask the LLM to assemble views from the (values-free) corpus
    profile, cache them, and return the refreshed view set."""
    _guard()
    tid = get_current_tenant()
    uid = get_current_owner_user_pk()
    summary = proposals.propose_views(db, tenant_id=tid, owner_user_id=uid,
                                      user_email=getattr(user, "email", None))
    return {**summary, **views(db=db, user=user)}


@router.patch("/intelligence/views/{view_key}")
def update_view(view_key: str,
                body: dict = Body(...),
                db: Session = Depends(get_session),
                user: CurrentUser = Depends(get_current_user)) -> dict:
    """Pin or dismiss a saved/AI view (built-in views aren't persisted, so
    they can't be pinned/dismissed)."""
    _guard()
    tid = get_current_tenant()
    uid = get_current_owner_user_pk()
    sv = db.scalar(select(SavedView).where(
        SavedView.tenant_id == tid, SavedView.owner_user_id == uid,
        SavedView.view_key == view_key))
    if sv is None:
        raise HTTPException(status_code=404, detail="View not found")
    if "pinned" in body:
        sv.pinned = bool(body["pinned"])
    if "dismissed" in body:
        sv.dismissed = bool(body["dismissed"])
    db.commit()
    return {"viewKey": view_key, "pinned": sv.pinned, "dismissed": sv.dismissed}
