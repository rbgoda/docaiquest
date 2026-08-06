"""Dashboard data computation — given a widget spec + owner's documents,
compute the data payload for rendering. Reuses analytics_themes.py builders
under the hood for complex aggregations; lightweight widgets (kpi, feed) are
computed inline."""

from __future__ import annotations

from datetime import date

from app.analytics_themes import (
    build_health,
)
from app.orm import Document

# ── Widget kind → computation ────────────────────────────────────────────────

_KIND_HANDLERS: dict[str, callable] = {}


def compute_widget(spec: dict, docs: list[Document], db) -> dict:
    """Compute the data for a single widget spec. Returns a dict with at least
    `{kind, title, data}` where `data` is the renderer-specific payload."""
    kind = spec.get("kind", "kpi")
    handler = _KIND_HANDLERS.get(kind, _compute_fallback)
    return handler(spec, docs, db)


def _register(kind: str):
    def deco(fn):
        _KIND_HANDLERS[kind] = fn
        return fn
    return deco


# ── Helpers ──────────────────────────────────────────────────────────────────


def _filter_docs(docs: list[Document], spec: dict) -> list[Document]:
    """Filter docs by the widget's config — docTypes whitelist + optional
    docIds list + optional months timeframe."""
    config = spec.get("config", {}) or {}
    doc_types = config.get("docTypes") or []
    doc_ids = config.get("docIds") or []
    months = config.get("months") or 0

    out = docs
    if doc_types:
        out = [d for d in out if d.doc_type in doc_types]
    if doc_ids:
        out = [d for d in out if d.id_external in doc_ids]
    if months and months > 0:
        cutoff = date.today().replace(day=1)
        for _ in range(int(months) - 1):
            if cutoff.month == 1:
                cutoff = cutoff.replace(year=cutoff.year - 1, month=12)
            else:
                cutoff = cutoff.replace(month=cutoff.month - 1)
        out = [d for d in out if d.created_at and d.created_at.date() >= cutoff]
    return out


def _collect_fields(docs: list[Document]) -> list[dict]:
    """Collect all inner-field key/value pairs across documents, tagged with
    doc metadata."""
    rows: list[dict] = []
    for d in docs:
        ef = d.extracted_fields or {}
        inner = ef.get("fields") if isinstance(ef.get("fields"), dict) else ef
        if not isinstance(inner, dict):
            continue
        for k, v in inner.items():
            rows.append({
                "docId": d.id_external, "docName": d.name, "docType": d.doc_type,
                "key": k, "value": v,
            })
    return rows


# ── Kind handlers ────────────────────────────────────────────────────────────


@_register("kpi")
def _compute_kpi(spec: dict, docs: list[Document], db) -> dict:
    config = spec.get("config", {}) or {}
    metric = config.get("metric", "")
    filtered = _filter_docs(docs, spec)
    fields = _collect_fields(filtered)

    value = None
    sub = f"{len(filtered)} document{'s' if len(filtered) != 1 else ''}"

    if metric == "doc_count":
        value = len(filtered)
    elif metric == "total_spend":
        total = 0.0
        for f in fields:
            if f["key"].lower() in ("total_amount", "total", "amount", "grand_total", "sum"):
                try:
                    total += float(str(f["value"]).replace(",", "").replace("$", "").replace("S$", "").replace("₹", ""))
                except (ValueError, TypeError):
                    pass
        value = f"${total:,.0f}" if total else None
    elif metric == "report_count":
        value = len(filtered)
    elif metric == "out_of_range":
        count = 0
        for f in fields:
            if f["key"].lower() in ("out_of_range", "abnormal", "flagged") and str(f["value"]).lower() in ("true", "yes", "1"):
                count += 1
        value = count
    elif metric == "needs_attention":
        from app.intelligence import alerts as alert_engine
        al = alert_engine.evaluate(filtered)
        value = len(al)
    else:
        # Generic: count
        value = len(filtered)

    return {"kind": "kpi", "title": spec.get("title"), "value": str(value) if value is not None else "—",
            "subtitle": sub, "config": config}


@_register("feed")
def _compute_feed(spec: dict, docs: list[Document], db) -> dict:
    """Alert/event feed — uses the alerts engine + watchlist for a filtered doc set."""
    filtered = _filter_docs(docs, spec)
    from app.intelligence import alerts as alert_engine
    intel_alerts = alert_engine.evaluate(filtered)

    items: list[dict] = []
    for a in intel_alerts[:20]:
        items.append({
            "title": a.get("title"), "detail": a.get("detail"),
            "docId": a.get("documentId"), "docName": a.get("documentName"),
            "severity": a.get("severity"), "type": a.get("type"),
            "dueAt": a.get("dueAt"), "daysDelta": a.get("daysDelta"),
        })
    return {"kind": "feed", "title": spec.get("title"), "items": items,
            "total": len(intel_alerts)}


@_register("donut")
def _compute_donut(spec: dict, docs: list[Document], db) -> dict:
    config = spec.get("config", {}) or {}
    field = config.get("field", "")
    filtered = _filter_docs(docs, spec)
    fields = _collect_fields(filtered)

    segments: dict[str, float] = {}
    for f in fields:
        if field and f["key"].lower() != field.lower():
            continue
        cat = str(f.get("value") or "Other")[:40]
        # Try to parse numeric value
        try:
            amt = float(str(f.get("value", "0")).replace(",", "").replace("$", "").replace("S$", "").replace("₹", ""))
        except (ValueError, TypeError):
            amt = 1  # count
        segments[cat] = segments.get(cat, 0) + amt

    sorted_segs = sorted(segments.items(), key=lambda x: -x[1])[:8]
    total = sum(v for _, v in sorted_segs)
    return {"kind": "donut", "title": spec.get("title"),
            "segments": [{"label": k, "value": v,
                          "pct": round(v / total * 100) if total else 0}
                         for k, v in sorted_segs],
            "total": round(total, 2)}


@_register("bars")
def _compute_bars(spec: dict, docs: list[Document], db) -> dict:
    config = spec.get("config", {}) or {}
    field = config.get("field", "")
    filtered = _filter_docs(docs, spec)
    fields = _collect_fields(filtered)

    groups: dict[str, float] = {}
    for f in fields:
        if field and f["key"].lower() != field.lower():
            continue
        label = str(f.get("value") or "Other")[:40]
        try:
            amt = float(str(f.get("value", "0")).replace(",", "").replace("$", "").replace("S$", "").replace("₹", ""))
        except (ValueError, TypeError):
            amt = 1
        groups[label] = groups.get(label, 0) + amt

    sorted_bars = sorted(groups.items(), key=lambda x: -x[1])[:10]
    max_val = max((v for _, v in sorted_bars), default=1)
    return {"kind": "bars", "title": spec.get("title"),
            "bars": [{"label": k, "value": v,
                      "pct": round(v / max_val * 100) if max_val else 0}
                     for k, v in sorted_bars]}


@_register("trend")
def _compute_trend(spec: dict, docs: list[Document], db) -> dict:
    config = spec.get("config", {}) or {}
    filtered = _filter_docs(docs, spec)

    # Group docs by month
    by_month: dict[str, float] = {}
    for d in filtered:
        if not d.created_at:
            continue
        m = d.created_at.strftime("%Y-%m")
        ef = d.extracted_fields or {}
        inner = ef.get("fields") if isinstance(ef.get("fields"), dict) else ef
        if not isinstance(inner, dict):
            continue
        for k, v in inner.items():
            try:
                amt = float(str(v).replace(",", "").replace("$", "").replace("S$", "").replace("₹", ""))
            except (ValueError, TypeError):
                continue
            by_month[m] = by_month.get(m, 0) + amt

    points = [{"date": k, "value": round(v, 2)}
              for k, v in sorted(by_month.items())[-24:]]
    return {"kind": "trend", "title": spec.get("title"), "points": points}


@_register("comparison")
def _compute_comparison(spec: dict, docs: list[Document], db) -> dict:
    config = spec.get("config", {}) or {}
    metric = config.get("metric", "")
    filtered = _filter_docs(docs, spec)

    if metric == "balance_check" and filtered:
        # Try to get A, L, E from balance sheets
        total_assets = total_liabilities = total_equity = 0.0
        for d in filtered:
            ef = d.extracted_fields or {}
            inner = ef.get("fields") if isinstance(ef.get("fields"), dict) else ef
            if not isinstance(inner, dict):
                continue
            for k, v in inner.items():
                try:
                    amt = float(str(v).replace(",", "").replace("$", "").replace("S$", "").replace("₹", ""))
                except (ValueError, TypeError):
                    continue
                kl = k.lower()
                if "asset" in kl and "total" in kl:
                    total_assets += amt
                elif "liabilit" in kl and "total" in kl:
                    total_liabilities += amt
                elif "equity" in kl and "total" in kl:
                    total_equity += amt
        return {"kind": "comparison", "title": spec.get("title"),
                "left": {"label": "Assets", "value": f"${total_assets:,.0f}"},
                "right": {"label": "Liabilities + Equity",
                          "value": f"${total_liabilities + total_equity:,.0f}"},
                "match": abs(total_assets - (total_liabilities + total_equity)) < 1.0}

    # Generic comparison: latest vs previous
    return {"kind": "comparison", "title": spec.get("title"),
            "left": {"label": "Current", "value": "—"},
            "right": {"label": "Previous", "value": "—"},
            "match": False}


@_register("table")
def _compute_table(spec: dict, docs: list[Document], db) -> dict:
    config = spec.get("config", {}) or {}
    filtered = _filter_docs(docs, spec)
    fields = _collect_fields(filtered)

    rows: list[dict] = []
    for f in fields[:50]:
        rows.append({"doc": f["docName"], "key": f["key"], "value": str(f["value"])[:80]})
    return {"kind": "table", "title": spec.get("title"),
            "columns": ["Document", "Field", "Value"], "rows": rows[:25]}


@_register("heatmap")
def _compute_heatmap(spec: dict, docs: list[Document], db) -> dict:
    """For health/lab reports — reuses the health builder's trend matrix."""
    filtered = _filter_docs(docs, spec)
    if not filtered:
        return {"kind": "heatmap", "title": spec.get("title"), "rows": [], "columns": []}
    try:
        result = build_health(filtered, None)
        sections = result.get("sections", [])
        # Extract the matrix section if present
        for s in sections:
            if s.get("kind") == "matrix":
                return {"kind": "heatmap", "title": spec.get("title"),
                        "rows": s.get("rows", []), "columns": s.get("columns", [])}
    except Exception:
        pass
    return {"kind": "heatmap", "title": spec.get("title"), "rows": [], "columns": []}


def _compute_fallback(spec: dict, docs: list[Document], db) -> dict:
    """Fallback for unknown widget kinds — return a simple summary."""
    filtered = _filter_docs(docs, spec)
    return {"kind": spec.get("kind", "unknown"), "title": spec.get("title"),
            "count": len(filtered), "message": f"{len(filtered)} documents match"}
