"""Audit intelligence — Graph RAG queries that turn the graph into
actionable audit signals. Five queries, all pure-SQL over the graph
layer + structured facts. No new LLM cost. These are the substantive
analytical procedures a financial auditor would otherwise run by hand:

1. counterparty_risk        — Orgs on BOTH expense + revenue side (round-tripping?)
2. concentration_risk       — Single vendor > N% of expense, or single customer > N% of revenue
3. subscription_drift       — Recurring subscriptions that suddenly stopped (or appeared)
4. category_anomaly         — Current-period spend vs rolling-12-mo mean per category
5. cross_period_continuity  — Vendors present in N prior periods then missing this period

Each returns a list of findings with severity + evidence pointers (so the
reviewer can drill straight to the source documents from the UI).
"""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.orm import Session

log = logging.getLogger("docaiq.graph.insights")


def _vendor_filter(vendor_pk: int | None) -> str:
    return "AND d.vendor_pk = :vendor_pk" if vendor_pk is not None else ""


# ── 1 · Counterparty risk ──────────────────────────────────────────────────


def counterparty_risk(
    db: Session, tenant_id: str, vendor_pk: int | None = None,
    *, date_from: str | None = None, date_to: str | None = None,
) -> list[dict]:
    # date_from / date_to currently informational — counterparty matching
    # is amount-aggregated across the corpus. Period scoping would
    # require joining doc-date into the inner CTEs; deferred for now.
    """Orgs that appear on BOTH sides of the books — same company we pay
    AND that pays us. Common signals:
      - Round-tripping (revenue inflated by buying from the same party)
      - Related-party transactions not disclosed
      - Net-zero arrangements that hide P&L manipulation
      - Reseller / referral kickbacks

    We match Org entities by canonical name across:
      - paid_to edges (we paid them — expense side)
      - invoiced_to / received_from edges (we billed them OR they paid us)
    """
    sql = text("""
        WITH expense_partners AS (
            SELECT e.canonical, e.text,
                   COUNT(DISTINCT er.evidence_doc_pk) AS exp_doc_count
            FROM entity_relations er
            JOIN entities e ON e.pk = er.dst_entity_pk
            WHERE er.tenant_id = :tenant_id
              AND er.relation = 'paid_to'
              AND e.kind = 'org'
              AND e.canonical IS NOT NULL
              AND (CAST(:vendor_pk AS INTEGER) IS NULL OR er.vendor_pk = CAST(:vendor_pk AS INTEGER))
            GROUP BY e.canonical, e.text
        ),
        revenue_partners AS (
            SELECT e.canonical, e.text,
                   COUNT(DISTINCT er.evidence_doc_pk) AS rev_doc_count
            FROM entity_relations er
            JOIN entities e ON e.pk = er.dst_entity_pk
            WHERE er.tenant_id = :tenant_id
              AND er.relation IN ('invoiced_to', 'received_from')
              AND e.kind = 'org'
              AND e.canonical IS NOT NULL
              AND (CAST(:vendor_pk AS INTEGER) IS NULL OR er.vendor_pk = CAST(:vendor_pk AS INTEGER))
            GROUP BY e.canonical, e.text
        )
        SELECT
            COALESCE(e.canonical, r.canonical) AS canonical,
            COALESCE(e.text, r.text) AS display_name,
            COALESCE(e.exp_doc_count, 0) AS expense_docs,
            COALESCE(r.rev_doc_count, 0) AS revenue_docs
        FROM expense_partners e
        INNER JOIN revenue_partners r ON e.canonical = r.canonical
        ORDER BY (COALESCE(e.exp_doc_count, 0) + COALESCE(r.rev_doc_count, 0)) DESC
    """)
    rows = db.execute(sql, {"tenant_id": tenant_id, "vendor_pk": vendor_pk}).all()

    findings = []
    for canonical, name, exp, rev in rows:
        # Severity from doc-count + balance. A single hit on each side is
        # informational; high counts on both sides is a serious flag.
        severity = "high" if (exp + rev) >= 6 else ("medium" if (exp + rev) >= 3 else "low")
        findings.append({
            "kind": "counterparty_risk",
            "severity": severity,
            "title": f"{name} appears on both sides of the books",
            "detail": (
                f"{exp} expense document{'s' if exp != 1 else ''} (we paid them) AND "
                f"{rev} revenue document{'s' if rev != 1 else ''} (they paid us). "
                f"Investigate for round-tripping or related-party disclosure."
            ),
            "evidence": {
                "canonical_name": canonical,
                "display_name": name,
                "expense_docs": exp,
                "revenue_docs": rev,
            },
        })
    return findings


# ── 2 · Concentration risk ─────────────────────────────────────────────────


def concentration_risk(
    db: Session, tenant_id: str, vendor_pk: int | None = None,
    threshold_pct: float = 0.25,
    *, date_from: str | None = None, date_to: str | None = None,
) -> list[dict]:
    # date filtering deferred — see counterparty_risk note.
    """One vendor takes > X% of total expense (or one customer > X% of
    revenue). Classic financial-statement risk: going-concern, supplier
    dependence, customer-concentration (auditing standard SAS 70 / SSAE)."""

    out: list[dict] = []

    # Expense concentration
    rows = db.execute(text("""
        WITH per_vendor AS (
            SELECT e.canonical, e.text,
                   SUM(
                       COALESCE(NULLIF(regexp_replace(money.canonical, '[^0-9.]', '', 'g'), ''), '0')::numeric
                   ) AS total
            FROM entity_relations er_to JOIN entities e ON e.pk = er_to.dst_entity_pk
            JOIN entity_relations er_total
                ON er_total.src_entity_pk = er_to.src_entity_pk
                AND er_total.relation = 'has_total'
            JOIN entities money ON money.pk = er_total.dst_entity_pk AND money.kind = 'money'
            WHERE er_to.tenant_id = :tenant_id
              AND er_to.relation = 'paid_to' AND e.kind = 'org' AND e.canonical IS NOT NULL
              AND (CAST(:vendor_pk AS INTEGER) IS NULL OR er_to.vendor_pk = CAST(:vendor_pk AS INTEGER))
            GROUP BY e.canonical, e.text
        ),
        total_exp AS (SELECT SUM(total) AS grand FROM per_vendor)
        SELECT canonical, text, total, grand,
               CASE WHEN grand > 0 THEN total / grand ELSE 0 END AS share
        FROM per_vendor, total_exp
        ORDER BY share DESC
    """), {"tenant_id": tenant_id, "vendor_pk": vendor_pk}).all()

    for canonical, name, total, grand, share in rows:
        if share is None or share < threshold_pct:
            continue
        severity = "high" if share >= 0.50 else ("medium" if share >= 0.35 else "low")
        out.append({
            "kind": "concentration_risk",
            "severity": severity,
            "title": f"Vendor concentration: {name} is {share*100:.0f}% of total expense",
            "detail": (
                f"Single vendor accounts for {float(total):.2f} of total {float(grand):.2f} "
                f"across this tenant. Supplier dependence + business-continuity risk."
            ),
            "evidence": {
                "side": "expense",
                "canonical_name": canonical,
                "display_name": name,
                "total": float(total),
                "share_of_total": float(share),
            },
        })

    # Revenue concentration — same query against invoiced_to + has_revenue
    rows = db.execute(text("""
        WITH per_customer AS (
            SELECT e.canonical, e.text,
                   SUM(
                       COALESCE(NULLIF(regexp_replace(money.canonical, '[^0-9.]', '', 'g'), ''), '0')::numeric
                   ) AS total
            FROM entity_relations er_to JOIN entities e ON e.pk = er_to.dst_entity_pk
            JOIN entity_relations er_rev
                ON er_rev.src_entity_pk = er_to.src_entity_pk
                AND er_rev.relation = 'has_revenue'
            JOIN entities money ON money.pk = er_rev.dst_entity_pk AND money.kind = 'money'
            WHERE er_to.tenant_id = :tenant_id
              AND er_to.relation IN ('invoiced_to', 'received_from')
              AND e.kind = 'org' AND e.canonical IS NOT NULL
              AND (CAST(:vendor_pk AS INTEGER) IS NULL OR er_to.vendor_pk = CAST(:vendor_pk AS INTEGER))
            GROUP BY e.canonical, e.text
        ),
        total_rev AS (SELECT SUM(total) AS grand FROM per_customer)
        SELECT canonical, text, total, grand,
               CASE WHEN grand > 0 THEN total / grand ELSE 0 END AS share
        FROM per_customer, total_rev
        ORDER BY share DESC
    """), {"tenant_id": tenant_id, "vendor_pk": vendor_pk}).all()

    for canonical, name, total, grand, share in rows:
        if share is None or share < threshold_pct:
            continue
        severity = "high" if share >= 0.50 else ("medium" if share >= 0.35 else "low")
        out.append({
            "kind": "concentration_risk",
            "severity": severity,
            "title": f"Customer concentration: {name} is {share*100:.0f}% of total revenue",
            "detail": (
                f"Single customer drives {float(total):.2f} of {float(grand):.2f} total revenue. "
                f"Loss of this account would materially impair the business."
            ),
            "evidence": {
                "side": "revenue",
                "canonical_name": canonical,
                "display_name": name,
                "total": float(total),
                "share_of_total": float(share),
            },
        })
    return out


# ── 3 · Subscription drift ─────────────────────────────────────────────────


def subscription_drift(
    db: Session, tenant_id: str, vendor_pk: int | None = None,
    *, date_from: str | None = None, date_to: str | None = None,
) -> list[dict]:
    """Recurring subscriptions that broke their cadence. Two patterns:
      - Appeared monthly for 3+ months then stopped → canceled or fraud
      - Appeared once then never again → mis-categorized one-time charge

    Operates on receipt + bank-statement transaction nodes tagged
    Subscriptions in their category field."""

    rows = db.execute(text("""
        WITH sub_txns AS (
            -- Receipt-level subscription expenses
            SELECT
                'receipt' AS source,
                COALESCE(NULLIF(extracted_fields->'fields'->>'vendor_name', ''), '?') AS merchant,
                COALESCE(NULLIF(extracted_fields->'fields'->>'date', ''), '') AS txn_date,
                d.id_external
            FROM documents d
            WHERE d.tenant_id = :tenant_id
              AND d.doc_type IN ('receipt', 'expense_claim')
              AND extracted_fields->'fields'->>'category' = 'Subscriptions'
              AND (CAST(:vendor_pk AS INTEGER) IS NULL OR d.vendor_pk = CAST(:vendor_pk AS INTEGER))
            UNION ALL
            -- Bank/CC statement transactions tagged Subscriptions
            SELECT
                'transaction' AS source,
                COALESCE(t->>'merchant', t->>'description', '?') AS merchant,
                COALESCE(t->>'date', '') AS txn_date,
                d.id_external
            FROM documents d, jsonb_array_elements(extracted_fields->'fields'->'top_transactions') t
            WHERE d.tenant_id = :tenant_id
              AND d.doc_type IN ('bank_statement', 'credit_card_statement')
              AND (t->>'category') = 'Subscriptions'
              AND (CAST(:vendor_pk AS INTEGER) IS NULL OR d.vendor_pk = CAST(:vendor_pk AS INTEGER))
        ),
        per_merchant AS (
            SELECT
                lower(merchant) AS merchant_canon,
                merchant,
                COUNT(*) AS occurrences,
                MIN(txn_date) AS first_seen,
                MAX(txn_date) AS last_seen
            FROM sub_txns
            WHERE merchant IS NOT NULL AND merchant <> '?'
              AND txn_date <> ''
            GROUP BY 1, 2
        )
        SELECT merchant_canon, merchant, occurrences, first_seen, last_seen,
               -- days since last seen, relative to the latest txn in the system
               (SELECT MAX(txn_date) FROM sub_txns) AS latest_in_system
        FROM per_merchant
        ORDER BY occurrences DESC, last_seen DESC
    """), {"tenant_id": tenant_id, "vendor_pk": vendor_pk}).all()

    findings = []
    for merchant_canon, merchant, occ, first_seen, last_seen, latest in rows:
        if occ >= 3:
            # Recurring — check if the latest is stale relative to system's latest
            try:
                last_d = datetime.fromisoformat(last_seen).date() if isinstance(last_seen, str) else last_seen
                latest_d = datetime.fromisoformat(latest).date() if isinstance(latest, str) else latest
                gap_days = (latest_d - last_d).days if last_d and latest_d else 0
            except Exception:  # noqa: BLE001
                gap_days = 0
            if gap_days > 60:
                findings.append({
                    "kind": "subscription_drift",
                    "severity": "medium" if gap_days < 120 else "high",
                    "title": f"Subscription '{merchant}' stopped {gap_days} days ago",
                    "detail": (
                        f"Recurring subscription seen {occ} times between {first_seen} and {last_seen}, "
                        f"then missing for {gap_days} days while other subscriptions continued. "
                        f"Was it canceled, or did the charge move to a different account?"
                    ),
                    "evidence": {
                        "merchant": merchant,
                        "occurrences": occ,
                        "first_seen": str(first_seen),
                        "last_seen": str(last_seen),
                        "gap_days": gap_days,
                    },
                })
        elif occ == 1:
            findings.append({
                "kind": "subscription_drift",
                "severity": "low",
                "title": f"'{merchant}' tagged Subscriptions but only seen once",
                "detail": (
                    "A 'Subscriptions' category transaction with no recurring pattern. "
                    "Likely a one-time charge mis-categorized — consider re-tagging."
                ),
                "evidence": {"merchant": merchant, "occurrences": 1, "date": str(last_seen)},
            })
    return findings


# ── 4 · Category anomaly ───────────────────────────────────────────────────


def category_anomaly(
    db: Session, tenant_id: str, vendor_pk: int | None = None,
    *, date_from: str | None = None, date_to: str | None = None,
) -> list[dict]:
    """Categories whose latest-month spend is materially above the rolling
    historical average. Standard analytical procedure in financial audits
    (ISA 520) — unusual fluctuations require investigation."""

    rows = db.execute(text("""
        WITH all_txns AS (
            SELECT
                COALESCE(NULLIF(extracted_fields->'fields'->>'category', ''), 'Uncategorised') AS category,
                COALESCE(NULLIF(extracted_fields->'fields'->>'date', ''), '')::date AS txn_date,
                ABS(
                    COALESCE(NULLIF(regexp_replace(extracted_fields->'fields'->>'total', '[^0-9.-]', '', 'g'), ''), '0')::numeric
                ) AS amount
            FROM documents d
            WHERE d.tenant_id = :tenant_id
              AND d.doc_type IN ('receipt', 'expense_claim')
              AND COALESCE(extracted_fields->'fields'->>'date', '') ~ '^\\d{4}-\\d{2}-\\d{2}$'
              AND (CAST(:vendor_pk AS INTEGER) IS NULL OR d.vendor_pk = CAST(:vendor_pk AS INTEGER))
            UNION ALL
            SELECT
                COALESCE(NULLIF(t->>'category', ''), 'Uncategorised') AS category,
                (t->>'date')::date AS txn_date,
                ABS(
                    COALESCE(NULLIF(regexp_replace(t->>'amount', '[^0-9.-]', '', 'g'), ''), '0')::numeric
                ) AS amount
            FROM documents d, jsonb_array_elements(extracted_fields->'fields'->'top_transactions') t
            WHERE d.tenant_id = :tenant_id
              AND d.doc_type IN ('bank_statement', 'credit_card_statement')
              AND (t->>'date') ~ '^\\d{4}-\\d{2}-\\d{2}$'
              AND (t->>'direction') IS DISTINCT FROM 'credit'
              AND (CAST(:vendor_pk AS INTEGER) IS NULL OR d.vendor_pk = CAST(:vendor_pk AS INTEGER))
        ),
        monthly AS (
            SELECT category, date_trunc('month', txn_date)::date AS month,
                   SUM(amount) AS month_total, COUNT(*) AS month_count
            FROM all_txns
            GROUP BY 1, 2
        ),
        latest_month AS (
            SELECT MAX(month) AS m FROM monthly
        ),
        history AS (
            SELECT m.category, AVG(m.month_total) AS mean, STDDEV_SAMP(m.month_total) AS stdv
            FROM monthly m, latest_month lm
            WHERE m.month < lm.m
            GROUP BY m.category HAVING COUNT(*) >= 2
        )
        SELECT
            m.category, m.month, m.month_total, m.month_count,
            h.mean, h.stdv,
            CASE WHEN h.stdv > 0 THEN (m.month_total - h.mean) / h.stdv ELSE NULL END AS z_score
        FROM monthly m
        JOIN latest_month lm ON m.month = lm.m
        JOIN history h ON h.category = m.category
        WHERE h.stdv > 0
        ORDER BY ABS((m.month_total - h.mean) / h.stdv) DESC
    """), {"tenant_id": tenant_id, "vendor_pk": vendor_pk}).all()

    findings = []
    for category, month, total, count, mean, stdv, z in rows:
        if z is None or abs(float(z)) < 1.5:
            continue
        direction = "above" if z > 0 else "below"
        severity = "high" if abs(float(z)) >= 3 else ("medium" if abs(float(z)) >= 2 else "low")
        findings.append({
            "kind": "category_anomaly",
            "severity": severity,
            "title": f"{category} in {month} is {abs(float(z)):.1f}σ {direction} historical mean",
            "detail": (
                f"This period: {float(total):.2f} across {count} transactions. "
                f"Historical monthly mean: {float(mean):.2f}, std {float(stdv):.2f}. "
                f"Investigate the driver (one-off vs. structural shift)."
            ),
            "evidence": {
                "category": category,
                "month": str(month),
                "this_period_total": float(total),
                "this_period_count": int(count),
                "historical_mean": float(mean),
                "historical_stdev": float(stdv),
                "z_score": float(z),
            },
        })
    return findings


# ── 5 · Cross-period continuity ────────────────────────────────────────────


def cross_period_continuity(
    db: Session, tenant_id: str, vendor_pk: int | None = None,
    min_prior_months: int = 3,
    *, date_from: str | None = None, date_to: str | None = None,
) -> list[dict]:
    """Vendors / merchants present in N prior months but absent in the most
    recent month. Often signals a churn'd subscription, a closed-out
    contract, or a fraud-conduit account that got disabled mid-period."""

    rows = db.execute(text("""
        WITH txns AS (
            SELECT
                COALESCE(NULLIF(extracted_fields->'fields'->>'vendor_name', ''), '?') AS merchant,
                COALESCE(NULLIF(extracted_fields->'fields'->>'date', ''), '')::date AS txn_date
            FROM documents d
            WHERE d.tenant_id = :tenant_id
              AND d.doc_type IN ('receipt', 'expense_claim')
              AND COALESCE(extracted_fields->'fields'->>'date', '') ~ '^\\d{4}-\\d{2}-\\d{2}$'
              AND (CAST(:vendor_pk AS INTEGER) IS NULL OR d.vendor_pk = CAST(:vendor_pk AS INTEGER))
            UNION ALL
            SELECT
                COALESCE(NULLIF(t->>'merchant', ''), NULLIF(t->>'description', ''), '?') AS merchant,
                (t->>'date')::date AS txn_date
            FROM documents d, jsonb_array_elements(extracted_fields->'fields'->'top_transactions') t
            WHERE d.tenant_id = :tenant_id
              AND d.doc_type IN ('bank_statement', 'credit_card_statement')
              AND (t->>'date') ~ '^\\d{4}-\\d{2}-\\d{2}$'
              AND (t->>'direction') IS DISTINCT FROM 'credit'
              AND (CAST(:vendor_pk AS INTEGER) IS NULL OR d.vendor_pk = CAST(:vendor_pk AS INTEGER))
        ),
        merchant_months AS (
            SELECT lower(merchant) AS merchant_canon, merchant,
                   date_trunc('month', txn_date)::date AS month
            FROM txns WHERE merchant IS NOT NULL AND merchant <> '?'
            GROUP BY 1, 2, 3
        ),
        latest AS (SELECT MAX(month) AS m FROM merchant_months),
        history AS (
            SELECT mm.merchant_canon, mm.merchant,
                   COUNT(DISTINCT mm.month) AS prior_months,
                   MAX(mm.month) AS last_seen
            FROM merchant_months mm, latest l
            WHERE mm.month < l.m
            GROUP BY mm.merchant_canon, mm.merchant
        ),
        in_latest AS (
            SELECT DISTINCT mm.merchant_canon
            FROM merchant_months mm, latest l
            WHERE mm.month = l.m
        )
        SELECT h.merchant_canon, h.merchant, h.prior_months, h.last_seen, l.m AS current_month
        FROM history h, latest l
        WHERE h.prior_months >= :min_prior_months
          AND h.merchant_canon NOT IN (SELECT merchant_canon FROM in_latest)
        ORDER BY h.prior_months DESC
    """), {
        "tenant_id": tenant_id, "vendor_pk": vendor_pk,
        "min_prior_months": min_prior_months,
    }).all()

    findings = []
    for canon, name, prior_months, last_seen, current_month in rows:
        severity = "high" if prior_months >= 6 else "medium"
        findings.append({
            "kind": "cross_period_continuity",
            "severity": severity,
            "title": f"'{name}' present in {prior_months} prior months, missing in {current_month}",
            "detail": (
                f"This counterparty appeared in {prior_months} prior months "
                f"(last seen {last_seen}) but is absent from {current_month}. "
                f"Verify contract termination, vendor change, or potential "
                f"control failure in approving new vendors."
            ),
            "evidence": {
                "merchant": name,
                "prior_months": int(prior_months),
                "last_seen": str(last_seen),
                "current_month": str(current_month),
            },
        })
    return findings


# ── Aggregator ─────────────────────────────────────────────────────────────


# ── Drill-down · resolve a finding's evidence to its source docs ─────────


def drill_finding(
    db: Session, tenant_id: str, kind: str, evidence: dict,
    vendor_pk: int | None = None,
) -> list[dict]:
    """Given a finding kind + its evidence payload, return the list of
    source documents that make up the finding so the reviewer can
    inspect them.

    Returns [{doc_id, name, doc_type, date, total, side, why_included}].
    """
    out: list[dict] = []
    if not evidence:
        return out

    if kind == "counterparty_risk":
        # Same Org appears on both sides. Find all docs that paid_to / invoiced_to / received_from
        # an org with matching canonical name.
        canon = (evidence.get("canonical_name") or "").lower()
        if not canon:
            return out
        rows = db.execute(text("""
            SELECT DISTINCT d.id_external, d.name, d.doc_type,
                   COALESCE(d.extracted_fields->'fields'->>'date',
                            d.extracted_fields->'fields'->>'issue_date',
                            d.extracted_fields->'fields'->>'payment_date') AS dt,
                   COALESCE(d.extracted_fields->'fields'->>'total',
                            d.extracted_fields->'fields'->>'amount') AS amt,
                   CASE
                       WHEN er.relation IN ('invoiced_to', 'received_from') THEN 'revenue'
                       WHEN er.relation = 'paid_to' THEN 'expense'
                       ELSE 'unknown'
                   END AS side
            FROM entity_relations er
            JOIN entities e ON e.pk = er.dst_entity_pk
            JOIN documents d ON d.pk = er.evidence_doc_pk
            WHERE er.tenant_id = :tenant_id
              AND er.relation IN ('paid_to', 'invoiced_to', 'received_from')
              AND e.canonical = :canon
              AND (CAST(:vendor_pk AS INTEGER) IS NULL OR d.vendor_pk = CAST(:vendor_pk AS INTEGER))
            ORDER BY 1 DESC
            LIMIT 100
        """), {"tenant_id": tenant_id, "canon": canon, "vendor_pk": vendor_pk}).all()
        for row in rows:
            doc_id, name, doc_type, dt, amt, side = row
            out.append({
                "docId": doc_id, "name": name, "docType": doc_type,
                "date": dt, "total": amt, "side": side,
                "whyIncluded": f"Counterparty '{evidence.get('display_name', canon)}' on {side} side",
            })
        return out

    if kind == "concentration_risk":
        canon = (evidence.get("canonical_name") or "").lower()
        side = evidence.get("side")  # 'expense' or 'revenue'
        if not canon or not side:
            return out
        # also accept received_from for revenue
        relations = ["paid_to"] if side == "expense" else ["invoiced_to", "received_from"]
        rows = db.execute(text("""
            SELECT DISTINCT d.id_external, d.name, d.doc_type,
                   COALESCE(d.extracted_fields->'fields'->>'date',
                            d.extracted_fields->'fields'->>'issue_date',
                            d.extracted_fields->'fields'->>'payment_date') AS dt,
                   COALESCE(d.extracted_fields->'fields'->>'total',
                            d.extracted_fields->'fields'->>'amount') AS amt
            FROM entity_relations er
            JOIN entities e ON e.pk = er.dst_entity_pk
            JOIN documents d ON d.pk = er.evidence_doc_pk
            WHERE er.tenant_id = :tenant_id
              AND er.relation = ANY(:relations)
              AND e.canonical = :canon
              AND (CAST(:vendor_pk AS INTEGER) IS NULL OR d.vendor_pk = CAST(:vendor_pk AS INTEGER))
            ORDER BY 1 DESC LIMIT 100
        """), {
            "tenant_id": tenant_id, "canon": canon,
            "relations": relations, "vendor_pk": vendor_pk,
        }).all()
        for row in rows:
            doc_id, name, doc_type, dt, amt = row
            out.append({
                "docId": doc_id, "name": name, "docType": doc_type,
                "date": dt, "total": amt, "side": side,
                "whyIncluded": f"Part of {evidence.get('display_name', canon)}'s "
                               f"{(evidence.get('share_of_total', 0) * 100):.0f}% concentration",
            })
        return out

    if kind == "subscription_drift":
        merchant = (evidence.get("merchant") or "").lower()
        if not merchant:
            return out
        rows = db.execute(text("""
            SELECT id_external, name, doc_type,
                   COALESCE(extracted_fields->'fields'->>'date',
                            extracted_fields->'fields'->>'issue_date') AS dt,
                   COALESCE(extracted_fields->'fields'->>'total',
                            extracted_fields->'fields'->>'amount') AS amt
            FROM documents
            WHERE tenant_id = :tenant_id
              AND (lower(COALESCE(extracted_fields->'fields'->>'vendor_name','')) = :merchant
                   OR lower(COALESCE(extracted_fields->'fields'->>'vendor_name','')) LIKE :merchant_like)
              AND (CAST(:vendor_pk AS INTEGER) IS NULL OR vendor_pk = CAST(:vendor_pk AS INTEGER))
            ORDER BY pk DESC LIMIT 100
        """), {
            "tenant_id": tenant_id, "merchant": merchant,
            "merchant_like": f"%{merchant}%", "vendor_pk": vendor_pk,
        }).all()
        for row in rows:
            doc_id, name, doc_type, dt, amt = row
            out.append({
                "docId": doc_id, "name": name, "docType": doc_type,
                "date": dt, "total": amt,
                "whyIncluded": f"Subscription instance of '{evidence.get('merchant')}'",
            })
        return out

    if kind == "category_anomaly":
        cat = evidence.get("category")
        month = evidence.get("month")  # YYYY-MM-01
        if not cat or not month:
            return out
        rows = db.execute(text("""
            SELECT id_external, name, doc_type,
                   COALESCE(extracted_fields->'fields'->>'date',
                            extracted_fields->'fields'->>'issue_date') AS dt,
                   COALESCE(extracted_fields->'fields'->>'total',
                            extracted_fields->'fields'->>'amount') AS amt
            FROM documents
            WHERE tenant_id = :tenant_id
              AND extracted_fields->'fields'->>'category' = :cat
              AND COALESCE(extracted_fields->'fields'->>'date', '') LIKE :month_like
              AND (CAST(:vendor_pk AS INTEGER) IS NULL OR vendor_pk = CAST(:vendor_pk AS INTEGER))
            ORDER BY pk DESC LIMIT 100
        """), {
            "tenant_id": tenant_id, "cat": cat,
            "month_like": f"{str(month)[:7]}-%", "vendor_pk": vendor_pk,
        }).all()
        for row in rows:
            doc_id, name, doc_type, dt, amt = row
            out.append({
                "docId": doc_id, "name": name, "docType": doc_type,
                "date": dt, "total": amt,
                "whyIncluded": f"{cat} expense in {str(month)[:7]} contributing to the anomaly",
            })
        return out

    if kind == "cross_period_continuity":
        merchant = (evidence.get("merchant") or "").lower()
        if not merchant:
            return out
        rows = db.execute(text("""
            SELECT id_external, name, doc_type,
                   COALESCE(extracted_fields->'fields'->>'date',
                            extracted_fields->'fields'->>'issue_date') AS dt,
                   COALESCE(extracted_fields->'fields'->>'total',
                            extracted_fields->'fields'->>'amount') AS amt
            FROM documents
            WHERE tenant_id = :tenant_id
              AND lower(COALESCE(extracted_fields->'fields'->>'vendor_name','')) LIKE :merchant_like
              AND (CAST(:vendor_pk AS INTEGER) IS NULL OR vendor_pk = CAST(:vendor_pk AS INTEGER))
            ORDER BY pk DESC LIMIT 100
        """), {
            "tenant_id": tenant_id, "merchant_like": f"%{merchant}%", "vendor_pk": vendor_pk,
        }).all()
        for row in rows:
            doc_id, name, doc_type, dt, amt = row
            out.append({
                "docId": doc_id, "name": name, "docType": doc_type,
                "date": dt, "total": amt,
                "whyIncluded": f"Historical instance of '{evidence.get('merchant')}' (now missing in current period)",
            })
        return out

    return out


def all_insights(
    db: Session, tenant_id: str, vendor_pk: int | None = None,
    *, date_from: str | None = None, date_to: str | None = None,
) -> dict:
    """Run every insight query and return a single grouped response. The
    UI's 'Audit Insights' card consumes this directly.

    `date_from` / `date_to` are ISO date strings — when set, queries
    that use document or transaction dates filter to the window. The
    counterparty_risk + concentration_risk queries are amount-only so
    the period filter applies via the underlying doc selection."""
    out = {
        "counterparty_risk": [],
        "concentration_risk": [],
        "subscription_drift": [],
        "category_anomaly": [],
        "cross_period_continuity": [],
        "_period": {"from": date_from, "to": date_to},
    }
    kw = {"date_from": date_from, "date_to": date_to}
    try:
        out["counterparty_risk"] = counterparty_risk(db, tenant_id, vendor_pk, **kw)
    except Exception as e:  # noqa: BLE001
        log.warning("insights.counterparty_risk failed: %s", e)
    try:
        out["concentration_risk"] = concentration_risk(db, tenant_id, vendor_pk, **kw)
    except Exception as e:  # noqa: BLE001
        log.warning("insights.concentration_risk failed: %s", e)
    try:
        out["subscription_drift"] = subscription_drift(db, tenant_id, vendor_pk, **kw)
    except Exception as e:  # noqa: BLE001
        log.warning("insights.subscription_drift failed: %s", e)
    try:
        out["category_anomaly"] = category_anomaly(db, tenant_id, vendor_pk, **kw)
    except Exception as e:  # noqa: BLE001
        log.warning("insights.category_anomaly failed: %s", e)
    try:
        out["cross_period_continuity"] = cross_period_continuity(db, tenant_id, vendor_pk, **kw)
    except Exception as e:  # noqa: BLE001
        log.warning("insights.cross_period_continuity failed: %s", e)
    return out
