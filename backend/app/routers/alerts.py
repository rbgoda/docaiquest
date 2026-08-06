"""Unified Alerts — merged Assistant watchlist + Intelligence alert engine.

Single urgency-ranked feed of everything that needs the user's attention:
- date-based items (expiries, renewals, payment due dates, contract ends) from
  the Assistant's `_derive_items()`, with domain-specific suggestions + .ics
- rule-based alerts (overdue, expired, unclassified, low-confidence, low-OCR,
  ingestion failures) from `intelligence/alerts.py`

Deduplication: when both systems flag the same (document, field), the Assistant
version wins (richer — has suggestion + calendar download) but carries the
Intelligence severity tag as well.

    GET  /api/alerts/unified  →  the merged, deduplicated, urgency-ranked feed

Owner-scoped like everything else. Zero extra LLM.
"""
from __future__ import annotations

import datetime as _dt

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_current_tenant, get_session
from app.documents_scope import get_current_owner_user_pk
from app.intelligence import alerts as alert_engine
from app.orm import AlertRule, Document
from app.routers.assistant import _classify, _parse_date as _assistant_parse_date
from app.security import CurrentUser, require_role

router = APIRouter()

# ── Assistant-side helpers (replicating _derive_items logic) ─────────────────


def _derive_items(db: Session, owner: int) -> list[dict]:
    """Replicate assistant._derive_items but accept an explicit owner rather than
    reading the ContextVar — lets us call it from the unified endpoint without
    worrying about which router set the var."""
    q = select(Document).where(
        Document.ingestion_status == "ready",
        Document.owner_user_id == owner,
    )
    docs = db.scalars(q).all()
    today = _dt.date.today()
    items: list[dict] = []
    for d in docs:
        fields = ((d.extracted_fields or {}).get("fields") or {})
        for k, v in fields.items():
            if not isinstance(v, (str, int)):
                continue
            dt = _assistant_parse_date(str(v))
            if not dt:
                continue
            cls = _classify(k, d.doc_type or "", d.name or "")
            if not cls:
                continue
            kind, title, suggestion = cls
            days = (dt - today).days
            if days < -45:
                continue
            items.append({
                "docId": d.id_external, "docName": d.name, "docType": d.doc_type,
                "field": k, "kind": kind, "title": title,
                "date": dt.isoformat(), "daysUntil": days,
                "urgency": _urgency(days, kind), "suggestion": suggestion,
                "icsUrl": f"/api/assistant/event.ics?doc_id={d.id_external}&field={k}",
                "source": "watchlist",
            })
    items.sort(key=lambda x: (x["daysUntil"] < 0 and -1 or 0, x["date"]))
    return items


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


# ── Merge logic ─────────────────────────────────────────────────────────────


def _merge(watchlist: list[dict], intel_alerts: list[dict]) -> list[dict]:
    """Merge Assistant watchlist + Intelligence alerts, deduplicating on
    (documentId, field/type). Assistant items win for date-based overlaps
    because they carry suggestions + .ics downloads."""
    # Index watchlist items by (docId, field)
    wl_by_key: dict[tuple[str, str], dict] = {}
    for it in watchlist:
        key = (it["docId"], it.get("field") or "")
        wl_by_key[key] = it

    merged: list[dict] = []
    seen: set[tuple[str, str]] = set()

    # Watchlist items go in first (richer UX)
    for it in watchlist:
        key = (it["docId"], it.get("field") or "")
        seen.add(key)
        merged.append({**it, "source": "watchlist"})

    # Intelligence alerts — skip if same doc+field already covered by watchlist
    for a in intel_alerts:
        # Try to match on document + the underlying field name (if the alert has a
        # date-based type like "expired" or "due_soon" that overlaps)
        alert_field = a.get("field") or ""
        key = (a["documentId"], alert_field)
        if key in seen:
            continue
        # Also check if the alert's type overlaps with a watchlist kind —
        # "expired"/"expiring_soon" overlap with watchlist "expiry" kind,
        # "overdue"/"due_soon" overlap with watchlist "payment" kind
        atype = a.get("type", "")
        if atype in ("expired", "expiring_soon", "overdue", "due_soon"):
            # Check if watchlist already covers this doc with a date-based item
            already_covered = any(
                w["docId"] == a["documentId"]
                for w in watchlist
                if w.get("kind") in ("expiry", "payment", "renewal", "contract")
            )
            if already_covered:
                # Mark the existing watchlist item with the intelligence severity
                for w in merged:
                    if w["docId"] == a["documentId"] and w.get("kind") in (
                        "expiry", "payment", "renewal", "contract",
                    ):
                        w["intelSeverity"] = a.get("severity")
                        w["intelType"] = atype
                        break
                continue

        seen.add(key)
        merged.append({**a, "source": "intelligence"})

    # Sort: overdue/severe first, then by date
    _SEV = {"high": 0, "warn": 1, "review": 2, "overdue": 0, "urgent": 1,
            "soon": 2, "upcoming": 3, "info": 4}
    merged.sort(key=lambda x: (
        _SEV.get(x.get("severity") or x.get("urgency"), 9),
        x.get("daysUntil") if x.get("daysUntil") is not None else 10**6,
    ))
    return merged


# ── Rule-based alert evaluation ──────────────────────────────────────────────


def _evaluate_rules(db: Session, owner: int) -> list[dict]:
    """Generate alert items from the user's custom alert rules."""
    rules = list(db.scalars(
        select(AlertRule).where(
            AlertRule.tenant_id == get_current_tenant(),
            AlertRule.owner_user_id == owner,
            AlertRule.enabled.is_(True),
        )
    ).all())

    if not rules:
        return []

    # Load ready docs for this owner
    docs = list(db.scalars(
        select(Document).where(
            Document.owner_user_id == owner,
            Document.is_archived.is_(False),
            Document.ingestion_status == "ready",
        )
    ).all())

    today = _dt.date.today()
    items: list[dict] = []

    for rule in rules:
        cfg = rule.config or {}

        if rule.rule_type == "watch_docs":
            # Alert on any watched doc that has date fields approaching
            watched_ids = set(cfg.get("docIds") or [])
            for d in docs:
                if d.id_external not in watched_ids:
                    continue
                fields = ((d.extracted_fields or {}).get("fields") or {})
                for k, v in fields.items():
                    if not isinstance(v, (str, int)):
                        continue
                    dt = _assistant_parse_date(str(v))
                    if not dt:
                        continue
                    days = (dt - today).days
                    if days < -45 or days > 90:
                        continue
                    cls = _classify(k, d.doc_type or "", d.name or "")
                    if not cls:
                        continue
                    kind, title, suggestion = cls
                    items.append({
                        "docId": d.id_external, "docName": d.name,
                        "docType": d.doc_type, "field": k, "kind": kind,
                        "title": f"[{rule.name}] {title}",
                        "date": dt.isoformat(), "daysUntil": days,
                        "urgency": _urgency(days, kind),
                        "suggestion": suggestion,
                        "icsUrl": f"/api/assistant/event.ics?doc_id={d.id_external}&field={k}",
                        "source": "rule", "ruleId": rule.pk,
                    })

        elif rule.rule_type == "watch_types":
            # Alert on new documents of watched types (docs uploaded in last 7 days)
            watched_types = set(cfg.get("docTypes") or [])
            cutoff = today - _dt.timedelta(days=7)
            for d in docs:
                if d.doc_type not in watched_types:
                    continue
                if d.created_at and d.created_at.date() >= cutoff:
                    items.append({
                        "docId": d.id_external, "docName": d.name,
                        "docType": d.doc_type, "field": "",
                        "kind": "review", "title": f"[{rule.name}] New {d.doc_type}",
                        "date": d.created_at.strftime("%Y-%m-%d") if d.created_at else "",
                        "daysUntil": 0, "urgency": "info",
                        "suggestion": f"New {d.doc_type.replace('_', ' ')} added — review it.",
                        "source": "rule", "ruleId": rule.pk,
                    })

        elif rule.rule_type == "field_date":
            # Watch a specific field across all docs (or filtered by docTypes)
            field_name = cfg.get("fieldName", "")
            days_before = cfg.get("daysBefore", 30)
            doc_types = set(cfg.get("docTypes") or [])
            for d in docs:
                if doc_types and d.doc_type not in doc_types:
                    continue
                fields = ((d.extracted_fields or {}).get("fields") or {})
                for k, v in fields.items():
                    if k != field_name:
                        continue
                    if not isinstance(v, (str, int)):
                        continue
                    dt = _assistant_parse_date(str(v))
                    if not dt:
                        continue
                    days = (dt - today).days
                    if days < 0 or days > days_before:
                        continue
                    cls = _classify(k, d.doc_type or "", d.name or "")
                    kind, title, suggestion = (cls if cls else ("review", field_name, ""))
                    items.append({
                        "docId": d.id_external, "docName": d.name,
                        "docType": d.doc_type, "field": k, "kind": kind,
                        "title": f"[{rule.name}] {title}",
                        "date": dt.isoformat(), "daysUntil": days,
                        "urgency": _urgency(days, kind),
                        "suggestion": suggestion or f"{field_name} approaching in {days} days",
                        "icsUrl": f"/api/assistant/event.ics?doc_id={d.id_external}&field={k}",
                        "source": "rule", "ruleId": rule.pk,
                    })

    # Deduplicate by (docId, field, ruleId)
    seen: set[tuple] = set()
    unique: list[dict] = []
    for it in items:
        key = (it["docId"], it.get("field") or "", it.get("ruleId"))
        if key not in seen:
            seen.add(key)
            unique.append(it)

    return unique


# ── Rule CRUD endpoints ──────────────────────────────────────────────────────


@router.get("/alerts/rules")
def list_rules(
    db: Session = Depends(get_session),
    _user: CurrentUser = Depends(require_role("admin", "reviewer")),
) -> dict:
    """List the user's custom alert rules."""
    owner = get_current_owner_user_pk()
    rules = list(db.scalars(
        select(AlertRule).where(
            AlertRule.tenant_id == get_current_tenant(),
            AlertRule.owner_user_id == owner,
        ).order_by(AlertRule.created_at.desc())
    ).all())
    return {
        "rules": [
            {
                "id": r.pk, "name": r.name, "ruleType": r.rule_type,
                "config": r.config, "enabled": r.enabled,
                "createdAt": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rules
        ],
    }


@router.post("/alerts/rules")
def create_rule(
    body: dict = Body(...),
    db: Session = Depends(get_session),
    _user: CurrentUser = Depends(require_role("admin", "reviewer")),
) -> dict:
    """Create a user-defined alert rule."""
    owner = get_current_owner_user_pk()
    name = str(body.get("name") or "Untitled rule")[:200]
    rule_type = str(body.get("ruleType") or "watch_docs")
    if rule_type not in ("watch_docs", "watch_types", "field_date"):
        raise HTTPException(status_code=400, detail=f"Unknown rule_type: {rule_type}")
    config = body.get("config") if isinstance(body.get("config"), dict) else {}

    rule = AlertRule(
        tenant_id=get_current_tenant(),
        owner_user_id=owner,
        name=name,
        rule_type=rule_type,
        config=config,
        enabled=True,
    )
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return {
        "rule": {
            "id": rule.pk, "name": rule.name, "ruleType": rule.rule_type,
            "config": rule.config, "enabled": rule.enabled,
            "createdAt": rule.created_at.isoformat() if rule.created_at else None,
        },
    }


@router.delete("/alerts/rules/{rule_id}")
def delete_rule(
    rule_id: int,
    db: Session = Depends(get_session),
    _user: CurrentUser = Depends(require_role("admin", "reviewer")),
) -> dict:
    """Delete a user-defined alert rule."""
    owner = get_current_owner_user_pk()
    rule = db.scalar(select(AlertRule).where(
        AlertRule.tenant_id == get_current_tenant(),
        AlertRule.pk == rule_id,
        AlertRule.owner_user_id == owner,
    ))
    if rule is None:
        raise HTTPException(status_code=404, detail="Rule not found")
    db.delete(rule)
    db.commit()
    return {"deleted": True}


# ── Endpoint ────────────────────────────────────────────────────────────────


@router.get("/alerts/unified")
def unified_alerts(
    db: Session = Depends(get_session),
    _user: CurrentUser = Depends(require_role("admin", "reviewer")),
) -> dict:
    """Merged, deduplicated feed of everything needing the user's attention."""
    owner = get_current_owner_user_pk()
    if owner is None:
        return {"items": [], "counts": {}, "kpis": {}}

    # 1. Assistant watchlist (date-based)
    watchlist = _derive_items(db, owner)

    # 2. Intelligence alerts (rule-based)
    q = select(Document).where(
        Document.owner_user_id == owner,
        Document.is_archived.is_(False),
    ).order_by(Document.pk.desc())
    docs = list(db.scalars(q).all())
    intel_alerts = alert_engine.evaluate(docs)

    # 3. User-defined alert rules
    rule_alerts = _evaluate_rules(db, owner)

    # 4. Merge + deduplicate (watchlist wins over intel; rules are additive)
    items = _merge(watchlist, intel_alerts)
    # Append rule alerts — they're user-defined so show them even if overlapping
    existing_keys = {(it["docId"], it.get("field") or "") for it in items}
    for ra in rule_alerts:
        key = (ra["docId"], ra.get("field") or "")
        if key not in existing_keys:
            existing_keys.add(key)
            items.append(ra)
    items.sort(key=lambda x: (
        {"overdue": 0, "urgent": 1, "soon": 2, "upcoming": 3, "info": 4}.get(
            x.get("urgency") or x.get("severity"), 9
        ),
        x.get("daysUntil") if x.get("daysUntil") is not None else 10**6,
    ))

    # 4. Counts
    buckets: dict[str, list] = {
        "overdue": [], "urgent": [], "soon": [], "upcoming": [], "info": [],
        "high": [], "warn": [], "review": [],
    }
    for it in items:
        urg = it.get("urgency") or _severity_to_urgency(it.get("severity"))
        if urg in buckets:
            buckets[urg].append(it)
        sev = it.get("severity")
        if sev in buckets:
            buckets[sev].append(it)

    # KPI strip
    ready = sum(1 for d in docs if d.ingestion_status == "ready")
    needs_attention = len({it["docId"] for it in items})
    overdue_count = len(buckets.get("overdue", [])) + len(buckets.get("high", []))

    return {
        "items": items,
        "counts": {
            "overdue": len(buckets.get("overdue", [])),
            "urgent": len(buckets.get("urgent", [])),
            "soon": len(buckets.get("soon", [])),
            "upcoming": len(buckets.get("upcoming", [])),
            "info": len(buckets.get("info", [])),
            "high": len(buckets.get("high", [])),
            "warn": len(buckets.get("warn", [])),
            "review": len(buckets.get("review", [])),
        },
        "total": len(items),
        "kpis": {
            "totalDocs": len(docs),
            "readyDocs": ready,
            "needsAttention": needs_attention,
            "overdueCount": overdue_count,
        },
    }


def _severity_to_urgency(severity: str | None) -> str:
    if severity == "high":
        return "overdue"
    if severity == "warn":
        return "soon"
    if severity == "review":
        return "info"
    return "info"
