"""On-demand analytics dashboards — document-type-driven.

The Analytics tab is a *builder*: each dashboard is a **theme** defined by the
document types that feed it (Financial ← bank/brokerage/statements, Expense ←
receipts/bills/invoices, …). A theme is offered only when the owner has matching
documents; the user ticks which of those documents to include, and we aggregate
their `extracted_fields` into the theme's dashboard payload.

The payload is a small typed shape the frontend renders generically:
  {theme, label, currency, docCount,
   metrics: [{label, value, unit, sub}],
   sections: [ {kind:"bars"|"trend"|"table", title, ...} ]}
so adding a theme = declaring its doc-types + a builder, with no new frontend.

Aggregation reads already-extracted fields. Money is tolerant of both numbers
(4320.4) and strings ("4,320.40SGD"). The Expense theme fills missing categories
(credit-card / bank transactions arrive without one) via the shared merchant
categorizer — cache-first, so it's free after the first sighting of a merchant.
"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, datetime
from typing import Any, Callable

# ── tolerant parsers ────────────────────────────────────────────────────────

_CCY_CODES = {"SGD", "USD", "EUR", "GBP", "INR", "AUD", "JPY", "CNY", "HKD",
              "MYR", "CHF", "CAD", "AED", "NZD", "THB", "PHP", "IDR", "KRW"}
_CCY_SYMS = {"$": "USD", "€": "EUR", "£": "GBP", "₹": "INR", "¥": "JPY", "S$": "SGD"}


def parse_money(v: Any) -> tuple[float | None, str | None]:
    """→ (amount, currency|None). Handles 4320.4, '4,320.40SGD', 'S$1,200', '(38.50)'."""
    if v is None or isinstance(v, bool):
        return None, None
    if isinstance(v, (int, float)):
        return float(v), None
    s = str(v).strip()
    if not s:
        return None, None
    ccy = None
    up = s.upper()
    for c in _CCY_CODES:
        if c in up:
            ccy = c
            break
    if ccy is None:
        # longer symbols first so "S$" (SGD) wins over "$" (USD)
        for sym in sorted(_CCY_SYMS, key=len, reverse=True):
            if sym in s:
                ccy = _CCY_SYMS[sym]
                break
    neg = s.strip().startswith("(") and s.strip().endswith(")")  # accounting negatives
    num = re.sub(r"[^0-9.\-]", "", s.replace(",", ""))
    if num in ("", "-", ".", "--"):
        return None, ccy
    try:
        amt = float(num)
    except ValueError:
        return None, ccy
    return (-abs(amt) if neg else amt), ccy


def parse_date(v: Any) -> date | None:
    if not v or not isinstance(v, str):
        return None
    s = v.strip()[:10]
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _fields(ef: Any) -> dict:
    """Pull the flat fields dict out of a Document.extracted_fields blob."""
    if not isinstance(ef, dict):
        return {}
    f = ef.get("fields")
    return f if isinstance(f, dict) else ef


def _first(f: dict, *keys: str) -> Any:
    for k in keys:
        if k in f and f[k] not in (None, "", []):
            return f[k]
    return None


def _rows(f: dict, *keys: str) -> list[dict]:
    for k in keys:
        v = f.get(k)
        if isinstance(v, list) and v and isinstance(v[0], dict):
            return v
    return []


def _dominant_currency(seen: list[str | None]) -> str | None:
    c = [x for x in seen if x]
    if not c:
        return None
    return max(set(c), key=c.count)


def _month(d: date | None) -> str | None:
    return d.strftime("%Y-%m") if d else None


def _in_window(dstr: Any, since: date | None) -> bool:
    """Timeframe filter. Keeps everything when no window is set; within a window,
    keeps items dated on/after `since` AND undated items (a missing date can't be
    proven out of range — better to show it than silently drop it)."""
    if since is None:
        return True
    d = parse_date(dstr)
    return d is None or d >= since


# ── theme registry ──────────────────────────────────────────────────────────
# doc_type → theme. A doc can belong to several themes (e.g. credit_card_statement).

FINANCIAL_TYPES = {
    "bank_statement", "financial_report", "brokerage_statement", "investment_statement",
    "capital_gains_statement", "statement_of_account", "payslip", "mortgage_statement",
    "loan_agreement", "crypto_transaction", "remittance_advice", "credit_card_statement",
    "revenue_invoice", "dividend_statement", "portfolio_statement",
}
EXPENSE_TYPES = {
    "receipt", "invoice", "commercial_invoice", "proforma_invoice", "subscription_invoice",
    "credit_card_statement", "utility_bill", "internet_bill", "phone_bill", "medical_bill",
    "property_tax_bill", "purchase_order", "expense_report", "bill",
}
ACCOUNTING_TYPES = {
    "balance_sheet", "income_statement", "profit_and_loss", "profit_loss", "pnl",
    "cash_flow_statement", "trial_balance", "general_ledger", "journal_entry",
    "financial_statement", "statement_of_account", "annual_report", "ledger",
}
IDENTITY_TYPES = {
    "passport", "national_id", "driver_license", "driver_licence", "social_security_card",
    "birth_certificate", "visa", "residence_permit", "residence_card", "voter_id",
    "work_permit", "aadhaar_card", "identity_card", "citizen_card",
}

_ID_TYPE_LABEL = {
    "national_id": "National ID", "driver_license": "Driver licence", "driver_licence": "Driver licence",
    "social_security_card": "SSN card", "residence_permit": "Residence permit", "voter_id": "Voter ID",
    "work_permit": "Work permit", "aadhaar_card": "Aadhaar", "birth_certificate": "Birth certificate",
}

# Friendly category label when an expense doc carries no explicit category.
_TYPE_CATEGORY = {
    "utility_bill": "Utilities", "internet_bill": "Internet", "phone_bill": "Phone",
    "medical_bill": "Healthcare", "property_tax_bill": "Property tax",
    "subscription_invoice": "Subscriptions", "receipt": "General",
}


def _money_number(v: float | None) -> float:
    return round(v, 2) if isinstance(v, (int, float)) else 0.0


# ── Financial builder ───────────────────────────────────────────────────────

def build_financial(docs: list[Any], *, categorize: Callable | None = None,
                    since: date | None = None) -> dict:
    holdings: list[dict] = []
    txns: list[dict] = []
    income = 0.0
    ccy_seen: list[str | None] = []

    for d in docs:
        f = _fields(d.extracted_fields)
        for h in _rows(f, "holdings", "positions", "investments"):
            val, c = parse_money(_first(h, "current_value", "market_value", "value"))
            ccy_seen.append(c)
            if val is not None:
                holdings.append({
                    "ticker": str(_first(h, "ticker", "symbol") or ""),
                    "name": str(_first(h, "company_name", "name", "description") or ""),
                    "shares": _first(h, "shares", "quantity", "units"),
                    "value": _money_number(val),
                    "trend": _first(h, "one_year_trend", "ytd", "change"),
                })
        for t in _rows(f, "transactions", "activity", "entries"):
            amt, c = parse_money(_first(t, "amount", "value"))
            ccy_seen.append(c)
            if amt is not None:
                txns.append({
                    "date": _first(t, "date", "posted_date", "transaction_date"),
                    "desc": str(_first(t, "description", "narrative", "type") or ""),
                    "amount": _money_number(amt),
                })
        # income-style docs (payslip): net/gross pay
        net, c = parse_money(_first(f, "net_pay", "net_salary", "take_home"))
        if net is not None:
            income += net
            ccy_seen.append(c)

    if since:
        txns = [t for t in txns if _in_window(t.get("date"), since)]

    currency = _dominant_currency(ccy_seen)
    metrics: list[dict] = []
    sections: list[dict] = []

    if holdings:
        pv = round(sum(h["value"] for h in holdings), 2)
        top = max(holdings, key=lambda h: h["value"])
        metrics.append({"label": "Portfolio value", "value": pv, "unit": currency, "sub": f"{len(holdings)} holdings"})
        metrics.append({"label": "Largest holding", "value": top["ticker"] or top["name"][:12] or "—",
                        "unit": "", "sub": f"{currency or ''} {top['value']:,.0f}".strip()})
        top_h = sorted(holdings, key=lambda h: h["value"], reverse=True)[:8]
        sections.append({"kind": "donut", "title": "Portfolio allocation", "unit": currency,
                         "items": [{"label": h["ticker"] or h["name"][:14] or "—", "value": h["value"]} for h in top_h]})
        sections.append({"kind": "table", "title": "Holdings",
                         "columns": ["Ticker", "Name", "Shares", "Value", "1y"],
                         "rows": [[h["ticker"] or "—", h["name"][:28] or "—",
                                   _fmt_num(h["shares"]), f"{h['value']:,.2f}", _fmt_pct(h["trend"])]
                                  for h in sorted(holdings, key=lambda h: h["value"], reverse=True)[:20]]})

    if txns:
        parsed = [(parse_date(t["date"]), t) for t in txns]
        inflow = round(sum(t["amount"] for _, t in parsed if t["amount"] > 0), 2)
        outflow = round(sum(-t["amount"] for _, t in parsed if t["amount"] < 0), 2)
        metrics.append({"label": "Net cash movement", "value": round(inflow - outflow, 2),
                        "unit": currency, "sub": f"{len(txns)} transactions"})
        if inflow or outflow:
            sections.append({"kind": "bars", "title": "Cash in vs out", "unit": currency,
                             "items": [{"label": "Inflow", "value": round(inflow, 2), "color": "#3FA47A"},
                                       {"label": "Outflow", "value": round(outflow, 2), "color": "#D8625E"}]})
        by_month: dict[str, float] = defaultdict(float)
        for dt, t in parsed:
            m = _month(dt)
            if m:
                by_month[m] += t["amount"]
        if by_month:
            pts = sorted(by_month.items())
            sections.append({"kind": "trend", "title": "Net cash flow by month", "unit": currency,
                             "points": [{"label": _month_label(m), "value": round(v, 2)} for m, v in pts]})
            # cumulative net cash flow (running total → wealth trajectory)
            run = 0.0
            cum = []
            for m, v in pts:
                run += v
                cum.append({"label": _month_label(m), "value": round(run, 2)})
            if len(cum) >= 2:
                sections.append({"kind": "trend", "title": "Cumulative cash flow", "unit": currency, "points": cum})
            # monthly inflow vs outflow as two lines
            in_m: dict[str, float] = defaultdict(float)
            out_m: dict[str, float] = defaultdict(float)
            for dt, t in parsed:
                m = _month(dt)
                if not m:
                    continue
                if t["amount"] > 0:
                    in_m[m] += t["amount"]
                else:
                    out_m[m] += -t["amount"]
            if len(pts) >= 2:
                months = [m for m, _ in pts]
                sections.append({"kind": "multitrend", "title": "Inflow vs outflow by month", "unit": currency,
                                 "series": [
                                     {"label": "Inflow", "color": "#3FB27F",
                                      "points": [{"label": _month_label(m), "value": round(in_m.get(m, 0.0), 2)} for m in months]},
                                     {"label": "Outflow", "color": "#E06C5E",
                                      "points": [{"label": _month_label(m), "value": round(out_m.get(m, 0.0), 2)} for m in months]}]})
        recent = sorted(parsed, key=lambda x: (x[0] or date.min), reverse=True)[:12]
        sections.append({"kind": "table", "title": "Recent transactions",
                         "columns": ["Date", "Description", "Amount"],
                         "rows": [[t["date"] or "—", t["desc"][:44] or "—", f"{t['amount']:,.2f}"]
                                  for _, t in recent]})

    if income:
        metrics.append({"label": "Income (payslips)", "value": round(income, 2), "unit": currency, "sub": ""})

    return {"currency": currency, "metrics": metrics, "sections": sections}


# ── Expense builder ─────────────────────────────────────────────────────────

def _clean_merchant(desc: Any) -> str:
    """Normalize a card/bank transaction description to a stable merchant key.

    Bank/card lines carry per-transaction reference codes ("PARKING.SG BILL_1A3F93",
    "ACRA* ARN260216001096", "SIMBATELECOM*125448734") that make every string unique
    → the merchant-category cache never hits → the categorizer re-calls the LLM on
    EVERY dashboard build (5-7s + cost). Stripping the reference tokens (anything with
    3+ digits) collapses them to the real merchant so the cache hits and repeat builds
    are instant. Also gives cleaner "Top merchants" labels.
    """
    s = re.sub(r"[*]", " ", str(desc or ""))
    toks = [t for t in s.split() if sum(ch.isdigit() for ch in t) < 3]
    cleaned = " ".join(toks).strip(" _-.")
    return cleaned or str(desc or "").strip()


def build_expense(docs: list[Any], *, categorize: Callable | None = None,
                  since: date | None = None) -> dict:
    items: list[dict] = []  # {date, merchant, category, amount}
    ccy_seen: list[str | None] = []

    for d in docs:
        f = _fields(d.extracted_fields)
        dt = (d.doc_type or "")
        # 1) statement/card transactions → one item per line
        line_txns = _rows(f, "transactions", "line_items", "items", "charges")
        if line_txns and dt in ("credit_card_statement", "bank_statement"):
            for t in line_txns:
                amt, c = parse_money(_first(t, "amount", "value", "total"))
                ccy_seen.append(c)
                if amt is not None and amt != 0:
                    items.append({
                        "date": _first(t, "date", "posted_date"),
                        "merchant": _clean_merchant(_first(t, "description", "merchant", "narrative"))[:40],
                        "category": str(_first(t, "category") or "Uncategorized"),
                        "amount": abs(_money_number(amt)),
                    })
            continue
        # 2) receipt / invoice / bill → one expense = the document total
        amt, c = parse_money(_first(f, "total_due", "total", "amount_due", "grand_total", "amount", "subtotal"))
        ccy_seen.append(c)
        if amt is not None:
            merchant = str(_first(f, "seller_name", "merchant", "store", "biller", "vendor",
                                  "supplier_name", "payee") or d.name or "")[:40]
            category = str(_first(f, "category", "expense_category")
                           or _TYPE_CATEGORY.get(dt) or "Uncategorized")
            items.append({
                "date": _first(f, "invoice_date", "date", "receipt_date", "issue_date", "bill_date"),
                "merchant": merchant, "category": category, "amount": abs(_money_number(amt)),
            })

    if since:
        items = [i for i in items if _in_window(i.get("date"), since)]

    # Fill missing categories with the shared merchant categorizer (cache-first;
    # one LLM batch for merchants not yet seen on this tenant). This is the SAME
    # logic the ingestion pipeline uses (app/agents/categorizer.py), so credit-card
    # and bank transactions — which arrive without a category — get classified the
    # same way receipts do, instead of falling into "Uncategorized".
    if categorize and items:
        txns = [{"description": it["merchant"],
                 "category": ("" if it["category"] == "Uncategorized" else it["category"])}
                for it in items]
        try:
            categorize(txns)
        except Exception:  # noqa: BLE001
            pass
        for it, t in zip(items, txns):
            c = (t.get("category") or "").strip()
            if c and c != "Other":
                it["category"] = c

    currency = _dominant_currency(ccy_seen)
    metrics: list[dict] = []
    sections: list[dict] = []

    if items:
        total = round(sum(i["amount"] for i in items), 2)
        by_cat: dict[str, float] = defaultdict(float)
        by_merch: dict[str, float] = defaultdict(float)
        by_month: dict[str, float] = defaultdict(float)
        for i in items:
            by_cat[i["category"]] += i["amount"]
            if i["merchant"]:
                by_merch[i["merchant"]] += i["amount"]
            m = _month(parse_date(i["date"]))
            if m:
                by_month[m] += i["amount"]
        top_cat = max(by_cat.items(), key=lambda kv: kv[1]) if by_cat else ("—", 0)
        metrics.append({"label": "Total spend", "value": total, "unit": currency, "sub": f"{len(items)} items"})
        metrics.append({"label": "Average", "value": round(total / len(items), 2), "unit": currency, "sub": "per item"})
        metrics.append({"label": "Top category", "value": top_cat[0], "unit": "",
                        "sub": f"{currency or ''} {top_cat[1]:,.0f}".strip()})

        sections.append({"kind": "donut", "title": "Spend by category", "unit": currency,
                         "items": [{"label": k, "value": round(v, 2)} for k, v in
                                   sorted(by_cat.items(), key=lambda kv: kv[1], reverse=True)[:8]]})
        if by_merch:
            sections.append({"kind": "bars", "title": "Top merchants", "unit": currency,
                             "items": [{"label": k, "value": round(v, 2)} for k, v in
                                       sorted(by_merch.items(), key=lambda kv: kv[1], reverse=True)[:8]]})
        if by_month:
            sections.append({"kind": "trend", "title": "Spend by month", "unit": currency,
                             "points": [{"label": _month_label(m), "value": round(v, 2)}
                                        for m, v in sorted(by_month.items())]})
        recent = sorted(items, key=lambda i: (parse_date(i["date"]) or date.min), reverse=True)[:15]
        sections.append({"kind": "table", "title": "Expenses",
                         "columns": ["Date", "Merchant", "Category", "Amount"],
                         "rows": [[i["date"] or "—", i["merchant"] or "—", i["category"], f"{i['amount']:,.2f}"]
                                  for i in recent]})

    return {"currency": currency, "metrics": metrics, "sections": sections}


# ── Identity builder ────────────────────────────────────────────────────────

def build_identity(docs: list[Any], *, categorize: Callable | None = None,
                   since: date | None = None) -> dict:
    people: set[str] = set()
    by_type: dict[str, int] = defaultdict(int)
    rows: list[dict] = []
    expiring = 0
    today = date.today()

    for d in docs:
        f = _fields(d.extracted_fields)
        dt = (d.doc_type or "")
        label = _ID_TYPE_LABEL.get(dt) or dt.replace("_", " ").title() or "ID"
        name = str(_first(f, "full_name", "name", "holder_name", "cardholder_name") or "").strip()
        num = str(_first(f, "id_number", "passport_number", "license_number", "licence_number",
                         "document_number", "number") or "").strip()
        nat = str(_first(f, "nationality", "country") or "").strip()
        exp = parse_date(_first(f, "expiry_date", "date_of_expiry", "valid_until", "expiration_date"))
        if name:
            people.add(name.lower())
        by_type[label] += 1
        if exp:
            days = (exp - today).days
            status = "Expired" if days < 0 else ("Expiring soon" if days < 180 else "Valid")
            if 0 <= days < 180 or days < 0:
                expiring += 1
        else:
            status = "—"
        rows.append({"holder": name or "—", "type": label, "number": num or "—",
                     "nat": nat or "—", "exp": exp.isoformat() if exp else "—", "status": status})

    metrics = [
        {"label": "Identity documents", "value": len(docs), "unit": "", "sub": ""},
        {"label": "People on file", "value": len(people), "unit": "", "sub": ""},
    ]
    if expiring:
        metrics.append({"label": "Need attention", "value": expiring, "unit": "", "sub": "expired / expiring soon"})

    sections: list[dict] = []
    if by_type:
        sections.append({"kind": "donut", "title": "By document type", "unit": None,
                         "items": [{"label": k, "value": v} for k, v in
                                   sorted(by_type.items(), key=lambda kv: kv[1], reverse=True)]})
    if rows:
        order = {"Expired": 0, "Expiring soon": 1, "Valid": 2, "—": 3}
        rows.sort(key=lambda r: (order.get(r["status"], 3), r["exp"]))
        sections.append({"kind": "table", "title": "Identity documents",
                         "columns": ["Holder", "Type", "Number", "Nationality", "Expiry", "Status"],
                         "rows": [[r["holder"], r["type"], r["number"], r["nat"], r["exp"], r["status"]] for r in rows]})
    return {"currency": None, "metrics": metrics, "sections": sections}


# ── Accounting builder ──────────────────────────────────────────────────────

def build_accounting(docs: list[Any], *, categorize: Callable | None = None,
                     since: date | None = None) -> dict:
    revenue = cogs = net = assets = liab = equity = 0.0
    ccy_seen: list[str | None] = []
    statements: list[dict] = []

    for d in docs:
        f = _fields(d.extracted_fields)

        def g(*keys):
            v, c = parse_money(_first(f, *keys))
            if c:
                ccy_seen.append(c)
            return v

        rev = g("revenue", "total_revenue", "sales", "turnover", "total_income")
        cg = g("cogs", "cost_of_goods_sold", "cost_of_sales")
        ni = g("net_income", "net_profit", "profit_after_tax", "net_earnings")
        ta = g("total_assets", "assets")
        tl = g("total_liabilities", "liabilities")
        te = g("total_equity", "equity", "shareholders_equity", "net_assets")
        revenue += rev or 0
        cogs += cg or 0
        net += ni or 0
        assets += ta or 0
        liab += tl or 0
        equity += te or 0
        check = None
        if ta is not None and tl is not None and te is not None:
            check = abs(ta - (tl + te)) <= max(1.0, ta * 0.01)
        statements.append({"type": (d.doc_type or "").replace("_", " ").title() or "Statement",
                           "name": d.name or "", "check": check})

    currency = _dominant_currency(ccy_seen)
    metrics: list[dict] = []
    sections: list[dict] = []
    if revenue:
        metrics.append({"label": "Revenue", "value": round(revenue, 2), "unit": currency, "sub": ""})
    if net:
        margin = f"{net / revenue * 100:.1f}% margin" if revenue else ""
        metrics.append({"label": "Net income", "value": round(net, 2), "unit": currency, "sub": margin})
    if assets:
        metrics.append({"label": "Total assets", "value": round(assets, 2), "unit": currency,
                        "sub": "A = L + E" if (liab or equity) else ""})

    if revenue:
        gp = round(revenue - cogs, 2)
        sections.append({"kind": "bars", "title": "Profit & loss", "unit": currency,
                         "items": [{"label": "Revenue", "value": round(revenue, 2), "color": "#3FA47A"},
                                   {"label": "Gross profit", "value": gp, "color": "#4BB4A5"},
                                   {"label": "Net income", "value": round(net, 2), "color": "#E0A23B"}]})
    if assets and (liab or equity):
        sections.append({"kind": "donut", "title": "How assets are financed", "unit": currency,
                         "items": [{"label": "Liabilities", "value": round(liab, 2)},
                                   {"label": "Equity", "value": round(equity, 2)}]})
    if statements:
        sections.append({"kind": "table", "title": "Statements checked",
                         "columns": ["Statement", "Document", "Balances"],
                         "rows": [[s["type"], (s["name"])[:38] or "—",
                                   "✓" if s["check"] else ("✗ off" if s["check"] is False else "—")]
                                  for s in statements]})
    return {"currency": currency, "metrics": metrics, "sections": sections}


# ── Health / lab builder ────────────────────────────────────────────────────

HEALTH_TYPES = {
    "lab_report", "lab_result", "medical_report", "blood_test", "pathology_report",
    "radiology_report", "health_checkup", "diagnostic_report", "test_report",
    # Classifier emits several surface forms for the same lab-report family — a doc
    # typed 'medical_lab_report' / 'laboratory_test_report' must land in Health too,
    # not just the exact 'lab_report' (product-feedback pk 54).
    "medical_lab_report", "laboratory_test_report", "laboratory_report", "lab_test",
    "medical_test_report", "clinical_report", "medical_lab_result", "pathology_result",
}


def _parse_test_value(s: Any) -> tuple[float | None, str | None, str | None]:
    """'3.20 [MMOL/L]' → (3.2,'MMOL/L',None); '5.5 [3.9-6.0 MMOL/L]' → (5.5,'MMOL/L','3.9-6.0');
    '13.9 gms/dl' → (13.9,'gms/dl',None)."""
    if s is None:
        return None, None, None
    s = str(s).strip()
    if not s:
        return None, None, None
    embedded_rng = None
    unit = None
    m = re.search(r"\[([^\]]*)\]", s)
    head = (s[:m.start()] if m else s).strip()
    bracket = m.group(1).strip() if m else ""
    vm = re.search(r"-?\d+(?:\.\d+)?", head)
    val = float(vm.group()) if vm else None
    if bracket:
        rm = re.search(r"\d+(?:\.\d+)?\s*(?:-|–|to)\s*\d+(?:\.\d+)?", bracket)
        if rm:
            embedded_rng = rm.group()
            unit = bracket.replace(rm.group(), "").strip() or None
        else:
            unit = bracket
    elif vm:
        unit = head[vm.end():].strip() or None
    return val, unit, embedded_rng


def _parse_range(s: Any) -> tuple[float | None, float | None]:
    if not s:
        return None, None
    s = str(s).strip()
    m = re.search(r"(-?\d+(?:\.\d+)?)\s*(?:-|–|to)\s*(-?\d+(?:\.\d+)?)", s)
    if m:
        return float(m.group(1)), float(m.group(2))
    m = re.search(r"(?:up\s*to|upto|<|<=|less than|max)\s*(\d+(?:\.\d+)?)", s, re.I)
    if m:
        return None, float(m.group(1))
    m = re.search(r"(?:>|>=|greater than|min|above)\s*(\d+(?:\.\d+)?)", s, re.I)
    if m:
        return float(m.group(1)), None
    return None, None


def _health_status(val: float | None, lo: float | None, hi: float | None) -> str:
    if val is None or (lo is None and hi is None):
        return "No range"
    if hi is not None and val > hi:
        return "High"
    if lo is not None and val < lo:
        return "Low"
    return "Normal"


def _pretty_test(s: Any) -> str:
    t = str(s or "").replace("_", " ").strip()
    return t[:1].upper() + t[1:] if t else ""


# test-name keyword → clinical panel (name, icon)
_HEALTH_PANELS = [
    ("Lipid Profile", "❤️", ["cholesterol", "ldl", "hdl", "triglycerid", "lipid", "vldl"]),
    ("Glucose Metabolism", "🩸", ["glucose", "hba1c", "glycated", "sugar", "insulin"]),
    ("Complete Blood Count", "🔬", ["haemoglobin", "hemoglobin", "erythrocyte", "rbc", "wbc",
        "leukocyte", "leucocyte", "platelet", "haematocrit", "hematocrit", "pcv", "mcv", "mch",
        "esr", "neutrophil", "lymphocyte", "monocyte", "eosinophil", "basophil"]),
    ("Liver Function", "🫁", ["alt", "ast", "sgpt", "sgot", "bilirubin", "alkaline phosphat",
        "alp", "protein", "albumin", "globulin", "ggt"]),
    ("Kidney Function", "🧫", ["creatinine", "urea", "bun", "egfr", "uric acid", "gfr"]),
    ("Thyroid", "🦋", ["tsh", "thyroid", "t3", "t4", "ft3", "ft4"]),
    ("Vitamins & Minerals", "☀️", ["vitamin", "b12", "folate", "ferritin", "iron", "calcium", "magnesium"]),
]


def _panel_for(name: str) -> tuple[str, str]:
    n = (name or "").lower()
    for pname, icon, kws in _HEALTH_PANELS:
        if any(k in n for k in kws):
            return pname, icon
    return "Other markers", "🧪"


def _short_test(name: str) -> str:
    t = str(name or "").strip()
    t = re.sub(r"\s*\([^)]*\)\s*$", "", t)      # drop trailing "(Colorimetric)" etc.
    t = t.replace("_", " ").strip()
    return (t[:1].upper() + t[1:]) if t else "Test"


# Canonical marker registry — maps lab-specific names/units to ONE comparable marker
# so reports from different labs/years line up into a single trend. Each:
#   (canon, panel, unit, ref_lo, ref_hi, aliases, mmol→unit factor)
# The factor converts a value reported in mmol/L to the canonical unit (lipids/glucose
# in mmol/L → mg/dL), so a 2021 SingHealth report (mmol/L) and a 2024 report (mg/dL) compare.
_CANON: list[tuple] = [
    ("Total Cholesterol", "Lipid Profile", "mg/dL", None, 200,
     ["cholesterol total", "total cholesterol", "t. cholesterol", "cholesterol, total"], 38.67),
    ("LDL Cholesterol", "Lipid Profile", "mg/dL", None, 100, ["ldl"], 38.67),
    ("HDL Cholesterol", "Lipid Profile", "mg/dL", 40, None, ["hdl"], 38.67),
    ("Triglycerides", "Lipid Profile", "mg/dL", None, 150, ["triglycerid"], 88.57),
    ("Fasting Glucose", "Glucose Metabolism", "mg/dL", 70, 100,
     ["glucose plasma fasting", "fasting glucose", "glucose fasting", "glucose, plasma", "blood sugar"], 18.0),
    ("HbA1c", "Glucose Metabolism", "%", None, 5.7, ["hba1c", "glycated", "a1c"], None),
    ("Hemoglobin", "Complete Blood Count", "g/dL", 13, 17, ["haemoglobin", "hemoglobin"], None),
    ("RBC Count", "Complete Blood Count", "mill/cmm", 4.5, 5.5, ["erythrocyte", "rbc"], None),
    ("WBC Count", "Complete Blood Count", "/cmm", 4000, 11000, ["leukocyte", "leucocyte", "wbc", "total wbc"], None),
    ("Platelets", "Complete Blood Count", "/cmm", 150000, 410000, ["platelet"], None),
    ("ESR", "Complete Blood Count", "mm/hr", 0, 20, ["esr"], None),
    ("Creatinine", "Kidney Function", "mg/dL", 0.7, 1.3, ["creatinine"], 88.4),
    ("Urea", "Kidney Function", "mg/dL", 15, 45, ["urea", "bun"], None),
    ("Uric Acid", "Kidney Function", "mg/dL", 3.5, 7.2, ["uric acid"], None),
    ("Total Protein", "Liver Function", "g/dL", 6.6, 8.7, ["total protein", "protein, serum"], None),
    ("Albumin", "Liver Function", "g/dL", 3.5, 5.2, ["albumin"], None),
    ("Bilirubin", "Liver Function", "mg/dL", None, 1.2, ["bilirubin"], None),
    ("ALT (SGPT)", "Liver Function", "IU/L", None, 45, ["alt", "sgpt"], None),
    ("AST (SGOT)", "Liver Function", "IU/L", None, 40, ["ast", "sgot"], None),
    ("TSH", "Thyroid", "uIU/mL", 0.4, 4.5, ["tsh"], None),
    ("Free T3", "Thyroid", "pg/mL", 2.3, 4.2, ["free t3", "ft3", "t3, free", "t3 free"], None),
    ("Free T4", "Thyroid", "ng/dL", 0.8, 1.8, ["free t4", "ft4", "t4, free", "t4 free"], None),
    ("Vitamin D", "Vitamins & Minerals", "ng/mL", 30, 100, ["vitamin d", "25-hydroxy", "25 oh"], None),
    ("Vitamin B12", "Vitamins & Minerals", "pg/mL", 200, 900, ["vitamin b12", "b12"], None),
]


def _canonicalize(name: str, val: float | None, unit: str | None,
                  lo: float | None, hi: float | None) -> dict | None:
    """Map a raw parsed test to a canonical marker (name/panel/unit/ref) with unit
    conversion, so the same analyte from different labs compares. Falls back to the
    raw marker (keeping any parsed range) when no canonical entry matches."""
    n = (name or "").lower()
    for canon, panel, unit_c, rlo, rhi, aliases, mmol_f in _CANON:
        if any(a in n for a in aliases):
            v = val
            if v is not None and mmol_f and unit and "mmol" in unit.lower():
                v = round(v * mmol_f, 1)
            return {"marker": canon, "panel": panel, "unit": unit_c, "val": v,
                    "lo": rlo, "hi": rhi, "canon": True}
    return {"marker": _short_test(name), "panel": _panel_for(name)[0], "unit": unit,
            "val": val, "lo": lo, "hi": hi, "canon": False}


def _status3(val: float | None, lo: float | None, hi: float | None) -> str:
    """3-tier status like the reference: Normal (green) · Borderline (yellow) · Abnormal (red)."""
    if val is None or (lo is None and hi is None):
        return "No range"
    if hi is not None:
        if val > hi * 1.15:
            return "Abnormal"
        if val > hi:
            return "Borderline"
    if lo is not None:
        if val < lo * 0.85:
            return "Abnormal"
        if val < lo:
            return "Borderline"
    return "Normal"


def _ref_str(lo: float | None, hi: float | None) -> str:
    if lo is not None and hi is not None:
        return f"{lo:g}–{hi:g}"
    if hi is not None:
        return f"< {hi:g}"
    if lo is not None:
        return f"> {lo:g}"
    return "—"


# Fields that carry the patient/person a health report is about, and the report's
# own date — used to group reports by person and to date otherwise-undated results.
_HEALTH_PERSON_KEYS = ("patient_name", "patient", "full_name", "name",
                       "holder_name", "member_name", "beneficiary_name", "beneficiary")
_HEALTH_DATE_KEYS = ("report_date", "collection_date", "collected_on", "sample_date",
                     "test_date", "primary_date", "date_of_issue", "issue_date", "date")


_TITLE_RX = re.compile(r"^(mr|mrs|ms|mstr|master|dr|miss|shri|smt)\.?\s+", re.I)


def _person_of(d: Any) -> str:
    """Best-effort patient/person name for a health doc, from its extracted fields.
    Empty string when unknown (those docs group under 'Unknown')."""
    v = _first(_fields(d.extracted_fields), *_HEALTH_PERSON_KEYS)
    if isinstance(v, list) and v:
        v = v[0]
    if isinstance(v, dict):
        v = v.get("name") or v.get("value")
    return str(v or "").strip()


def _person_key(name: str) -> str:
    """Normalise a patient name for grouping so honorifics and word order don't split
    one person: strip a leading title, drop punctuation, sort the remaining tokens.
    ('MR. B H GODA' and 'B H GODA' → 'b goda h'). Best-effort — real-world lab names
    are noisy (OCR variants, inconsistent PII redaction), so this only merges the
    clear cases; unresolved variants group separately rather than mis-merge people."""
    s = name.strip()
    prev = None
    while prev != s:                    # peel repeated honorifics ("Mr. Dr. …")
        prev, s = s, _TITLE_RX.sub("", s).strip()
    toks = sorted(t for t in re.sub(r"[^a-z0-9 ]", " ", s.lower()).split() if t)
    return " ".join(toks)


def _doc_date(d: Any) -> str | None:
    """The report's OWN date (document level) — the fallback when an individual
    result row carries no date, so single-date reports still form a trend point."""
    v = _first(_fields(d.extracted_fields), *_HEALTH_DATE_KEYS)
    if isinstance(v, (list, dict)) or not v:
        return None
    dt = parse_date(str(v))
    return dt.isoformat() if dt else str(v)[:10]


def _health_results(docs: list[Any], since: date | None) -> list[dict]:
    """Read canonicalizable test results from a set of health docs, falling back to
    the document's own date when a result row has none (feedback #56: single-date
    reports previously produced no trend)."""
    results: list[dict] = []
    for d in docs:
        f = _fields(d.extracted_fields)
        doc_date = _doc_date(d)
        # Curated lab_report schema stores results under `test_results[]` =
        # {name, result, unit, reference_range}, dated at the document level. This is
        # the shape most lab reports actually use — reading it is what makes the
        # dashboard non-empty (feedback #56: "no graph / no change over time").
        tr = f.get("test_results")
        if isinstance(tr, list) and tr:
            for t in tr:
                if not isinstance(t, dict):
                    continue
                # The curated schema isn't strictly enforced by the LLM, so the per-test
                # keys vary across reports (name/investigation/parameter, result/
                # observed_value/value, reference_range/biological_reference_interval).
                # Read the common variants so every lab report contributes.
                nm = _first(t, "name", "investigation", "test", "test_name", "parameter", "analyte", "description")
                res = _first(t, "result", "observed_value", "value", "observation", "reading", "amount")
                unit_raw = _first(t, "unit", "units")
                rng_raw = _first(t, "reference_range", "biological_reference_interval", "normal_range",
                                 "bio_reference_interval", "ref_range", "reference", "range")
                val, unit, emb = _parse_test_value(res)
                unit = (str(unit_raw or "").strip() or unit) or None
                lo, hi = _parse_range(rng_raw or emb)
                if val is not None:
                    results.append({"name": _pretty_test(nm) or "Test", "val": val,
                                    "unit": unit, "lo": lo, "hi": hi,
                                    "status": _health_status(val, lo, hi), "date": doc_date})
            continue
        # Universal schema fallback: records[] = {date, description, amount, attributes}.
        recs = f.get("records") if isinstance(f.get("records"), list) else []
        for r in recs:
            if not isinstance(r, dict):
                continue
            date_ = r.get("date") or doc_date
            desc = str(r.get("description") or "")
            amount = r.get("amount")
            attrs = r.get("attributes") if isinstance(r.get("attributes"), list) else []
            rng_attr = None
            value_attrs = []
            for a in attrs:
                if not isinstance(a, dict):
                    continue
                lab = str(a.get("label") or "").lower()
                if "reference" in lab or "range" in lab or "normal" in lab:
                    rng_attr = a.get("value")
                else:
                    value_attrs.append(a)
            if amount not in (None, "", 0, 0.0):
                val, unit, emb = _parse_test_value(amount)
                lo, hi = _parse_range(rng_attr or emb)
                if val is not None:
                    results.append({"name": desc or "Test", "val": val, "unit": unit,
                                    "lo": lo, "hi": hi, "status": _health_status(val, lo, hi), "date": date_})
            elif value_attrs:
                for a in value_attrs:
                    val, unit, emb = _parse_test_value(a.get("value"))
                    lo, hi = _parse_range(emb or rng_attr)
                    if val is not None:
                        nm = _pretty_test(a.get("label")) or desc or "Test"
                        results.append({"name": nm, "val": val, "unit": unit, "lo": lo, "hi": hi,
                                        "status": _health_status(val, lo, hi), "date": date_})
    if since:
        results = [r for r in results if _in_window(r.get("date"), since)]
    return results


def _health_sections(results: list[dict], doc_count: int, *, person: str | None = None,
                     prefix: bool = False) -> tuple[list[dict], list[dict]]:
    """Turn one person's (or the whole set's) results into metric cards + panel/matrix
    sections. `prefix` tags section titles with the person for the multi-person view."""
    canon_rows = []
    for r in results:
        c = _canonicalize(r["name"], r["val"], r["unit"], r["lo"], r["hi"])
        if c["val"] is None:
            continue
        c["date"] = str(r.get("date") or "")[:10]
        c["status"] = _status3(c["val"], c["lo"], c["hi"])
        canon_rows.append(c)
    if not canon_rows:
        return [], []

    dates = sorted({c["date"] for c in canon_rows if c["date"]})
    abnormal_markers = {c["marker"] for c in canon_rows if c["status"] in ("Abnormal", "Borderline")}
    span = f"{dates[0]} → {dates[-1]}" if len(dates) > 1 else (dates[0] if dates else "")
    lab_sub = f"{person} · {span}".strip(" ·") if (person and not prefix) else span
    metrics = [
        {"label": "Lab reports", "value": doc_count, "unit": "", "sub": lab_sub},
        {"label": "Markers tracked", "value": len({c["marker"] for c in canon_rows}),
         "unit": "", "sub": f"{len(dates)} test dates"},
        {"label": "Out of range", "value": len(abnormal_markers), "unit": "",
         "sub": "borderline / abnormal" if abnormal_markers else "all normal"},
    ]

    def _valstr(row):
        v = f"{row['val']:g}"
        return f"{v} {row['unit']}".strip() if row.get("unit") else v

    by_marker: dict[str, dict] = {}
    for c in canon_rows:
        m = by_marker.setdefault(c["marker"], {"panel": c["panel"], "unit": c["unit"],
                                               "lo": c["lo"], "hi": c["hi"], "pts": {}})
        m["pts"][c["date"]] = c

    _PORDER = {n: i for i, (n, _, _) in enumerate(_HEALTH_PANELS)}
    ICON = {n: ic for n, ic, _ in _HEALTH_PANELS}

    by_panel: dict[str, list] = defaultdict(list)
    for marker, m in by_marker.items():
        by_panel[m["panel"]].append((marker, m))
    panels_out = []
    for pname in sorted(by_panel, key=lambda n: _PORDER.get(n, 99)):
        tests, series, flag_n = [], [], 0
        for marker, m in by_panel[pname]:
            latest = m["pts"][max(m["pts"])]
            st = latest["status"]
            if st in ("Abnormal", "Borderline"):
                flag_n += 1
            tests.append({"name": marker, "value": _valstr(latest), "ref": _ref_str(m["lo"], m["hi"]), "status": st})
            if len(m["pts"]) >= 2:
                series.append({"label": marker, "points": [{"label": d[:7], "value": round(row["val"], 2)}
                                                           for d, row in sorted(m["pts"].items())]})
        note = f"{len(tests) - flag_n} of {len(tests)} markers in range"
        panels_out.append({"name": pname, "icon": ICON.get(pname, "🧪"),
                           "status": "Monitor" if flag_n else "Normal",
                           "tone": "watch" if flag_n else "good", "note": note, "series": series[:3],
                           "tests": sorted(tests, key=lambda t: (t["status"] not in ("Abnormal", "Borderline"), t["name"]))[:9]})
    tag = f"{person} · " if (prefix and person) else ""
    sections = [{"kind": "panels", "title": f"{tag}Status", "panels": panels_out}]
    if dates:
        mrows = []
        for marker, m in sorted(by_marker.items(), key=lambda kv: (_PORDER.get(kv[1]["panel"], 99), kv[0])):
            cells = [{"value": (f"{m['pts'][d]['val']:g}" if d in m["pts"] else ""),
                      "status": (m["pts"][d]["status"] if d in m["pts"] else "")} for d in dates]
            label = marker + (f" ({m['unit']})" if m["unit"] else "")
            mrows.append({"param": label, "ref": _ref_str(m["lo"], m["hi"]), "cells": cells})
        sections.append({"kind": "matrix", "title": f"{tag}Trend analysis",
                         "dates": [d[:7] for d in dates], "rows": mrows})
    return metrics, sections


def _merge_name_subsets(groups: dict, display: dict) -> None:
    """In-place · fold a name-group whose (≥2-token) key is a STRICT SUBSET of another
    group's key into that larger group — so 'Goda Rajesh' and 'Balvantrai Goda' collapse
    into 'Balvantrai Goda Rajesh' when a middle name is extracted inconsistently. The
    ≥2-shared-token requirement means a merely-shared surname ('Goda' alone) never
    collapses two distinct people (e.g. 'B H Goda' stays separate from 'Rajesh Goda')."""
    keys = [k for k in groups if k]
    toks = {k: set(k.split()) for k in keys}
    remap = {k: k for k in keys}
    for a in keys:
        best = None
        for b in keys:
            if a != b and len(toks[a]) >= 2 and toks[a] < toks[b]:
                if best is None or len(toks[b]) > len(toks[best]):
                    best = b
        if best:
            remap[a] = best

    def _root(k: str) -> str:
        seen = set()
        while remap[k] != k and k not in seen:
            seen.add(k)
            k = remap[k]
        return k

    for a in keys:
        r = _root(a)
        if r != a and a in groups:
            groups[r].extend(groups.pop(a))
            display.setdefault(r, display.get(a, r))
            display.pop(a, None)


def build_health(docs: list[Any], *, categorize: Callable | None = None,
                 since: date | None = None) -> dict:
    """Health/lab dashboard. Reports are grouped by PATIENT so different people's
    results never merge into one dataset (feedback #56); each marker trends over its
    report dates, falling back to the document date when a result row is undated."""
    # Group by a normalised name key so "RAJESH GODA" and "Rajesh Goda" are one person.
    groups: dict[str, list] = defaultdict(list)
    display: dict[str, str] = {}
    for d in docs:
        name = _person_of(d)
        key = _person_key(name)
        groups[key].append(d)
        if key and key not in display:
            display[key] = re.sub(r"\s+", " ", name).strip()
    _merge_name_subsets(groups, display)
    named = sorted(display, key=lambda k: display[k].lower())
    empty = {"currency": None, "metrics": [], "sections": [],
             "empty": "No dated test results could be read from these reports."}

    # One patient (or names unknown) → a single dataset, name surfaced in the metric.
    if len(named) <= 1:
        person = display[named[0]] if named else None
        metrics, sections = _health_sections(_health_results(docs, since), len(docs), person=person)
        return {"currency": None, "metrics": metrics, "sections": sections} if sections else empty

    # Multiple patients → one labelled block per person so trends stay separate.
    order = named + ([""] if groups.get("") else [])
    top = [
        {"label": "People", "value": len(named), "unit": "",
         "sub": ", ".join(display[k] for k in named[:4]) + ("…" if len(named) > 4 else "")},
        {"label": "Reports", "value": len(docs), "unit": "", "sub": f"{len(order)} groups"},
    ]
    out_sections: list[dict] = []
    for key in order:
        label = display.get(key, "Unknown")
        _, sections = _health_sections(_health_results(groups[key], since),
                                       len(groups[key]), person=label, prefix=True)
        out_sections.extend(sections)
    return {"currency": None, "metrics": top, "sections": out_sections} if out_sections else empty


# ── small formatters ────────────────────────────────────────────────────────

def _fmt_num(v: Any) -> str:
    n, _ = parse_money(v)
    if n is None:
        return str(v) if v not in (None, "") else "—"
    return f"{n:,.0f}" if n == int(n) else f"{n:,.2f}"


def _fmt_pct(v: Any) -> str:
    if isinstance(v, (int, float)):
        return f"{v * 100:+.1f}%" if abs(v) < 3 else f"{v:+.1f}%"
    return str(v) if v not in (None, "") else "—"


_MONTHS = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _month_label(ym: str) -> str:
    try:
        y, m = ym.split("-")
        return f"{_MONTHS[int(m)]} {y[2:]}"
    except Exception:  # noqa: BLE001
        return ym


# theme key → (label, description, icon, doc_types, builder)
THEMES: dict[str, dict] = {
    "financial": {
        "label": "Financial overview",
        "description": "Holdings, balances and cash flow from your bank, brokerage and investment statements.",
        "icon": "📈",
        "types": FINANCIAL_TYPES,
        "builder": build_financial,
    },
    "expense": {
        "label": "Expense overview",
        "description": "Spend by category, merchant and month from your receipts, bills and invoices.",
        "icon": "🧾",
        "types": EXPENSE_TYPES,
        "builder": build_expense,
    },
    "accounting": {
        "label": "Accounting overview",
        "description": "P&L, balance sheet and reconciliation from your accounting statements (P&L, balance sheet, trial balance).",
        "icon": "🧮",
        "types": ACCOUNTING_TYPES,
        "builder": build_accounting,
    },
    "identity": {
        "label": "Identity overview",
        "description": "Who's on file, ID numbers, nationalities and document expiries from your passports and ID cards.",
        "icon": "🪪",
        "types": IDENTITY_TYPES,
        "builder": build_identity,
    },
    "health": {
        "label": "Health & lab results",
        "description": "Test values vs reference ranges, what's out of range, and trends across your lab reports.",
        "icon": "🩺",
        "types": HEALTH_TYPES,
        "builder": build_health,
    },
}


def theme_for_type(doc_type: str | None) -> list[str]:
    """Which theme keys a doc_type feeds."""
    dt = (doc_type or "").lower()
    return [k for k, t in THEMES.items() if dt in t["types"]]
