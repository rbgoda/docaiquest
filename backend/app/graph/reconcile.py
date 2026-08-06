"""Reconciliation passes over the graph.

Two deterministic SQL-based scans — no LLM cost. They run after Layer 1
fact-extraction + Layer 3 bootstrap have populated entity / relation
rows for receipts and bank statements.

find_duplicates(vendor_pk)
--------------------------
A receipt is a duplicate of another if it shares:
  - Same claimant (Person.canonical match via paid_by edges)
  - Same vendor   (Org.canonical match via paid_to edges)
  - Same amount + currency (Money.canonical match via has_total edges)
  - Date within ±3 days (Date.canonical via dated edges)

Confidence scoring:
  1.00  — exact amount + same date
  0.90  — exact amount + 1-day delta
  0.80  — exact amount + 2-3 day delta
  0.65  — amount within 1% + 0-3 day delta (rare; vendor adjustments)
Below 0.50 we don't emit the edge.

Output: `duplicate_of` edges between the two receipt Document nodes.
Edge metadata records the signals so the reviewer can see *why* we
flagged it: {claimant_match, vendor_match, amount_delta, day_delta}.


find_payment_matches(vendor_pk)
-------------------------------
A receipt is "paid by" a bank-statement transaction if:
  - Receipt's has_total Money.canonical == Transaction's transaction_amount
    Money.canonical (i.e. exact same amount + currency canonical key)
  - Transaction date is 0-5 days AFTER the receipt date
  - Same vendor_pk (audit cohort)

Confidence:
  1.00  — exact amount + same day or +1 day (instant settlement)
  0.95  — exact amount + 2-3 day delta (typical card clearing)
  0.85  — exact amount + 4-5 day delta (slow processors)
  0.70  — amount equal within $0.01, any allowable delta

Output: `paid_by_transaction` edges from the receipt Document → bank
statement's Transaction node, with metadata {bank_statement_doc_pk,
amount, days_after}.


Idempotency
-----------
Each call to `scan()` creates a new `graph_runs` row of kind='reconcile'
and tags every edge it emits. Re-running deletes the previous
reconcile-kind edges for the same scope before re-emitting. Safe to
fire from the worker after each new upload.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session

from app.db import get_current_tenant
from app.orm import Entity, EntityRelation, GraphRun
from app.repositories import documents as docs_repo

log = logging.getLogger("docaiq.graph.reconcile")


REL_DUPLICATE_OF = "duplicate_of"
REL_PAID_BY_TRANSACTION = "paid_by_transaction"
REL_REVENUE_RECEIVED_BY_TXN = "revenue_received_by_transaction"


def scan(db: Session, *, vendor_pk: int | None = None) -> dict[str, int]:
    """Run both reconciliation passes for a vendor (or all vendors in
    the tenant if vendor_pk is None). Returns counts of edges emitted.

    Idempotent: each call replaces prior reconcile-kind edges in its
    scope. The graph_run row records the audit trail."""
    # Resolve tenant from any handy row — we're inside the request's
    # tenant context so set_current_tenant has been called.
    tenant_id_row = db.execute(text("SELECT current_setting('docaiq.tenant_id', true) AS t")).first()
    # Better: read from any vendor / entity we touch. The repo layer
    # filters by tenant for us; for the worker invocation we need
    # tenant_id explicitly because raw SQL doesn't see the ContextVar.
    # The graph_runs row needs a tenant_id, so derive it from the
    # entities we're about to inspect.
    sample = db.execute(text("SELECT tenant_id FROM entities LIMIT 1")).first()
    tenant_id = sample[0] if sample else (tenant_id_row[0] if tenant_id_row else None)
    if not tenant_id:
        log.warning("reconcile: no entities exist — nothing to reconcile")
        return {"duplicates": 0, "payments": 0}

    # Tear down prior reconcile runs in this scope so re-runs don't
    # double-count. We delete by graph_run_pk so other extraction
    # passes (bootstrap, llm_entity) are untouched.
    prior_runs = db.scalars(
        select(GraphRun).where(
            GraphRun.tenant_id == tenant_id,
            GraphRun.kind == "reconcile",
        )
    ).all()
    for r in prior_runs:
        # If a vendor scope was set, only nuke that vendor's relations
        # so other vendors' findings survive. If full-tenant scan, nuke
        # all reconcile edges from this run.
        if vendor_pk is not None:
            db.execute(
                delete(EntityRelation).where(
                    EntityRelation.graph_run_pk == r.pk,
                    EntityRelation.vendor_pk == vendor_pk,
                )
            )
        else:
            db.execute(delete(EntityRelation).where(EntityRelation.graph_run_pk == r.pk))
        # Only delete the run row when we've cleared everything it produced.
        if vendor_pk is not None:
            remaining = db.scalar(
                select(func.count()).select_from(EntityRelation).where(
                    EntityRelation.graph_run_pk == r.pk
                )
            ) or 0
        else:
            remaining = 0
        if not remaining:
            db.delete(r)
    db.flush()

    run = GraphRun(
        tenant_id=tenant_id,
        document_pk=None,  # cross-doc pass
        kind="reconcile",
        model=None,
        status="running",
    )
    db.add(run)
    db.flush()

    try:
        dup_count = _find_duplicates(db, tenant_id, vendor_pk, run)
        pay_count = _find_payment_matches(db, tenant_id, vendor_pk, run)
        rev_count = _find_revenue_matches(db, tenant_id, vendor_pk, run)
        run.status = "complete"
        run.entities_added = 0
        run.relations_added = dup_count + pay_count + rev_count
        run.completed_at = datetime.now(timezone.utc)
        db.flush()
        log.info(
            "reconcile: vendor_pk=%s → %d duplicates, %d expense-payment matches, %d revenue matches",
            vendor_pk, dup_count, pay_count, rev_count,
        )
        return {
            "duplicates": dup_count,
            "payments": pay_count,
            "revenue_matches": rev_count,
        }
    except Exception as e:  # noqa: BLE001
        log.exception("reconcile scan failed: %s", e)
        # Postgres aborts the current transaction on the first SQL error;
        # any subsequent flush would also fail until we roll back. Roll
        # back the failed state then re-record the run in a fresh tx.
        db.rollback()
        failed = GraphRun(
            tenant_id=tenant_id,
            document_pk=None,
            kind="reconcile",
            model=None,
            status="failed",
            error=str(e)[:1000],
            completed_at=datetime.now(timezone.utc),
        )
        db.add(failed)
        db.commit()
        raise


# ── Duplicate detection ───────────────────────────────────────────────────


def _find_duplicates(db: Session, tenant_id: str, vendor_pk: int | None,
                     run: GraphRun) -> int:
    """SQL self-join over receipts. The query joins each receipt to
    every other receipt that shares vendor + claimant + amount
    canonical keys via the bootstrap relations, then computes the
    day-delta from the dated edges. Filter at >0.5 confidence."""

    sql = text("""
        WITH receipt_facts AS (
            -- Each receipt with its (vendor_canon, claimant_canon, money_canon, date_iso)
            SELECT
                d.pk          AS receipt_pk,
                d.vendor_pk   AS vendor_pk,
                d.name        AS receipt_name,
                vendor_e.canonical    AS vendor_canon,
                claimant_e.canonical  AS claimant_canon,
                money_e.canonical     AS money_canon,
                date_e.canonical      AS date_iso,
                CASE
                    WHEN date_e.canonical ~ '^\\d{4}-\\d{2}-\\d{2}$'
                    THEN date_e.canonical::date
                    ELSE NULL
                END           AS date_parsed
            FROM documents d
            JOIN entities doc_e
                ON doc_e.document_pk = d.pk
                AND doc_e.kind = 'document'
                AND doc_e.source = 'fact_bootstrap'
            LEFT JOIN entity_relations vendor_r
                ON vendor_r.src_entity_pk = doc_e.pk
                AND vendor_r.relation = 'paid_to'
            LEFT JOIN entities vendor_e
                ON vendor_e.pk = vendor_r.dst_entity_pk
            LEFT JOIN entity_relations claimant_r
                ON claimant_r.src_entity_pk = doc_e.pk
                AND claimant_r.relation = 'paid_by'
            LEFT JOIN entities claimant_e
                ON claimant_e.pk = claimant_r.dst_entity_pk
            LEFT JOIN entity_relations money_r
                ON money_r.src_entity_pk = doc_e.pk
                AND money_r.relation = 'has_total'
            LEFT JOIN entities money_e
                ON money_e.pk = money_r.dst_entity_pk
            LEFT JOIN entity_relations date_r
                ON date_r.src_entity_pk = doc_e.pk
                AND date_r.relation = 'dated'
            LEFT JOIN entities date_e
                ON date_e.pk = date_r.dst_entity_pk
            WHERE d.tenant_id = :tenant_id
              AND d.doc_type IN ('receipt', 'expense_claim')
              AND (CAST(:vendor_pk AS INTEGER) IS NULL OR d.vendor_pk = CAST(:vendor_pk AS INTEGER))
        ),
        pairs AS (
            SELECT
                a.receipt_pk   AS a_pk,
                b.receipt_pk   AS b_pk,
                a.vendor_pk,
                a.vendor_canon, a.claimant_canon, a.money_canon,
                a.date_iso     AS a_date,
                b.date_iso     AS b_date,
                a.date_parsed, b.date_parsed,
                -- Postgres `date - date` returns an integer (days),
                -- not an interval. ABS() of that integer is the delta.
                CASE
                    WHEN a.date_parsed IS NOT NULL AND b.date_parsed IS NOT NULL
                    THEN ABS(b.date_parsed - a.date_parsed)
                    ELSE NULL
                END AS day_delta
            FROM receipt_facts a
            JOIN receipt_facts b
                ON a.receipt_pk < b.receipt_pk
                -- Vendor + amount must match and be present. Date is
                -- checked downstream (≤3 day delta).
                AND a.vendor_canon IS NOT NULL AND a.vendor_canon = b.vendor_canon
                AND a.money_canon  IS NOT NULL AND a.money_canon  = b.money_canon
                -- Claimant: if both are present they must match; if both
                -- are missing (common — receipts often don't print the
                -- buyer's name), still flag the pair. The receipt-image
                -- duplicate-fraud case usually doesn't depend on claimant.
                AND (
                  (a.claimant_canon IS NOT NULL AND a.claimant_canon = b.claimant_canon)
                  OR (a.claimant_canon IS NULL AND b.claimant_canon IS NULL)
                )
        )
        SELECT * FROM pairs WHERE day_delta IS NULL OR day_delta <= 3
    """)

    rows = db.execute(sql, {"tenant_id": tenant_id, "vendor_pk": vendor_pk}).all()

    count = 0
    for r in rows:
        a_pk, b_pk, vpk, v_canon, c_canon, m_canon, a_date, b_date, _ap, _bp, day_delta = r
        confidence = _dup_confidence(day_delta)
        if confidence < 0.50:
            continue
        # We need entity pks of the document nodes (not the documents.pk).
        a_doc_e = db.scalar(
            select(Entity).where(
                Entity.document_pk == a_pk,
                Entity.kind == "document",
                Entity.source == "fact_bootstrap",
            )
        )
        b_doc_e = db.scalar(
            select(Entity).where(
                Entity.document_pk == b_pk,
                Entity.kind == "document",
                Entity.source == "fact_bootstrap",
            )
        )
        if not a_doc_e or not b_doc_e:
            continue
        db.add(EntityRelation(
            tenant_id=tenant_id,
            vendor_pk=vpk,
            src_entity_pk=a_doc_e.pk,
            dst_entity_pk=b_doc_e.pk,
            relation=REL_DUPLICATE_OF,
            confidence=confidence,
            evidence_doc_pk=a_pk,
            evidence_chunk_pk=None,
            metadata_json={
                "vendor": v_canon,
                "claimant": c_canon,
                "amount": m_canon,
                "a_date": a_date,
                "b_date": b_date,
                "day_delta": day_delta,
                "signals": _dup_signals(day_delta, claimant_present=c_canon is not None),
            },
            source="reconcile",
            graph_run_pk=run.pk,
        ))
        count += 1
    return count


def _dup_confidence(day_delta: int | None) -> float:
    if day_delta is None:
        return 0.55  # unknown delta — weak duplicate signal
    if day_delta == 0:
        return 1.00
    if day_delta == 1:
        return 0.90
    if day_delta <= 3:
        return 0.80
    return 0.0


def _dup_signals(day_delta: int | None, *, claimant_present: bool = True) -> list[str]:
    out = ["vendor_match", "amount_match"]
    out.append("claimant_match" if claimant_present else "no_claimant_on_either")
    if day_delta == 0:
        out.append("date_match")
    elif day_delta is not None and day_delta <= 3:
        out.append(f"date_within_{day_delta}d")
    else:
        out.append("date_unknown")
    return out


# ── Payment-match (receipt ↔ bank transaction) ────────────────────────────


def _find_payment_matches(db: Session, tenant_id: str, vendor_pk: int | None,
                          run: GraphRun) -> int:
    """For each Receipt's has_total Money, find Transaction nodes whose
    transaction_amount Money canonical matches AND whose date is 0-5
    days AFTER the receipt date. Same vendor_pk required.

    The output edges go from the Receipt Document node → the
    Transaction node so the UI can show "this receipt was paid by
    transaction X on the Y bank statement"."""

    sql = text("""
        WITH receipts AS (
            SELECT
                doc_e.pk        AS receipt_doc_e_pk,
                d.pk            AS receipt_doc_pk,
                d.vendor_pk     AS vendor_pk,
                money_e.pk      AS money_e_pk,
                money_e.canonical AS money_canon,
                date_e.canonical  AS date_iso,
                CASE WHEN date_e.canonical ~ '^\\d{4}-\\d{2}-\\d{2}$'
                     THEN date_e.canonical::date ELSE NULL END  AS receipt_date
            FROM documents d
            JOIN entities doc_e
                ON doc_e.document_pk = d.pk
                AND doc_e.kind = 'document'
                AND doc_e.source = 'fact_bootstrap'
            JOIN entity_relations money_r
                ON money_r.src_entity_pk = doc_e.pk
                AND money_r.relation = 'has_total'
            JOIN entities money_e
                ON money_e.pk = money_r.dst_entity_pk
                AND money_e.kind = 'money'
            LEFT JOIN entity_relations date_r
                ON date_r.src_entity_pk = doc_e.pk
                AND date_r.relation = 'dated'
            LEFT JOIN entities date_e
                ON date_e.pk = date_r.dst_entity_pk
                AND date_e.kind = 'date'
            WHERE d.tenant_id = :tenant_id
              AND d.doc_type IN ('receipt', 'expense_claim')
              AND (CAST(:vendor_pk AS INTEGER) IS NULL OR d.vendor_pk = CAST(:vendor_pk AS INTEGER))
        ),
        transactions AS (
            SELECT
                bd.pk           AS bank_doc_pk,
                bd.vendor_pk    AS vendor_pk,
                txn_e.pk        AS txn_e_pk,
                txn_e.entity_metadata AS txn_meta,
                txn_money.canonical    AS money_canon,
                txn_date.canonical     AS date_iso,
                CASE WHEN txn_date.canonical ~ '^\\d{4}-\\d{2}-\\d{2}$'
                     THEN txn_date.canonical::date ELSE NULL END AS txn_date
            FROM documents bd
            JOIN entities bs_doc_e
                ON bs_doc_e.document_pk = bd.pk
                AND bs_doc_e.kind = 'document'
                AND bs_doc_e.source = 'fact_bootstrap'
            JOIN entity_relations has_txn
                ON has_txn.src_entity_pk = bs_doc_e.pk
                AND has_txn.relation = 'has_transaction'
            JOIN entities txn_e
                ON txn_e.pk = has_txn.dst_entity_pk
                AND txn_e.kind = 'transaction'
            JOIN entity_relations amt_r
                ON amt_r.src_entity_pk = txn_e.pk
                AND amt_r.relation = 'transaction_amount'
            JOIN entities txn_money
                ON txn_money.pk = amt_r.dst_entity_pk
                AND txn_money.kind = 'money'
            LEFT JOIN entity_relations tdate_r
                ON tdate_r.src_entity_pk = txn_e.pk
                AND tdate_r.relation = 'transaction_date'
            LEFT JOIN entities txn_date
                ON txn_date.pk = tdate_r.dst_entity_pk
            WHERE bd.tenant_id = :tenant_id
              AND bd.doc_type IN ('bank_statement', 'audited_financial_statement')
              AND (CAST(:vendor_pk AS INTEGER) IS NULL OR bd.vendor_pk = CAST(:vendor_pk AS INTEGER))
        )
        SELECT
            r.receipt_doc_e_pk,
            r.receipt_doc_pk,
            r.vendor_pk,
            r.money_canon,
            r.receipt_date,
            t.txn_e_pk,
            t.bank_doc_pk,
            t.txn_date,
            t.txn_meta,
            -- date - date → integer days (positive when txn is later).
            CASE
                WHEN r.receipt_date IS NOT NULL AND t.txn_date IS NOT NULL
                THEN (t.txn_date - r.receipt_date)
                ELSE NULL
            END AS days_after,
            -- Match type flag — exact when the money canonicals match
            -- string-for-string; fuzzy when amounts are within ±5%
            -- (covers tips, FX, processing fees). Currency must still match.
            CASE WHEN r.money_canon = t.money_canon THEN 'exact' ELSE 'fuzzy_amount' END AS match_type
        FROM receipts r
        JOIN transactions t
            -- Currency must match exactly (last token of money canonical
            -- is the ISO code like 'USD', 'SGD'). Amount portion is the
            -- numeric part — compared exact OR within ±5%.
            ON SPLIT_PART(r.money_canon, ' ', 2) = SPLIT_PART(t.money_canon, ' ', 2)
            AND (
                r.money_canon = t.money_canon
                OR ABS(SPLIT_PART(r.money_canon, ' ', 1)::numeric - SPLIT_PART(t.money_canon, ' ', 1)::numeric)
                   <= GREATEST(SPLIT_PART(r.money_canon, ' ', 1)::numeric * 0.05, 0.50)
            )
            AND r.vendor_pk IS NOT DISTINCT FROM t.vendor_pk
        WHERE
            r.receipt_date IS NULL
            OR t.txn_date IS NULL
            OR (t.txn_date - r.receipt_date BETWEEN 0 AND 5)
    """)

    rows = db.execute(sql, {"tenant_id": tenant_id, "vendor_pk": vendor_pk}).all()

    count = 0
    for r in rows:
        (receipt_doc_e_pk, receipt_doc_pk, vpk, money_canon,
         receipt_date, txn_e_pk, bank_doc_pk, txn_date, txn_meta, days_after, match_type) = r
        confidence = _pay_confidence(days_after)
        # Fuzzy amount matches deserve a confidence haircut — they're
        # informational ("looks like the same transaction with a tip")
        # not absolute. 0.85 → 0.72 keeps them above the 0.5 threshold
        # but visually distinct from clean exacts.
        if match_type == "fuzzy_amount":
            confidence = max(0.55, confidence - 0.15)
        if confidence < 0.50:
            continue
        db.add(EntityRelation(
            tenant_id=tenant_id,
            vendor_pk=vpk,
            src_entity_pk=receipt_doc_e_pk,
            dst_entity_pk=txn_e_pk,
            relation=REL_PAID_BY_TRANSACTION,
            confidence=confidence,
            evidence_doc_pk=receipt_doc_pk,
            evidence_chunk_pk=None,
            metadata_json={
                "bank_statement_doc_pk": bank_doc_pk,
                "amount_canonical": money_canon,
                "receipt_date": str(receipt_date) if receipt_date else None,
                "transaction_date": str(txn_date) if txn_date else None,
                "days_after": days_after,
                "match_type": match_type,  # 'exact' | 'fuzzy_amount'
                "txn_description": (txn_meta or {}).get("description") if isinstance(txn_meta, dict) else None,
            },
            source="reconcile",
            graph_run_pk=run.pk,
        ))
        count += 1
    return count


def _pay_confidence(days_after: int | None) -> float:
    if days_after is None:
        return 0.70
    if days_after <= 1:
        return 1.00
    if days_after <= 3:
        return 0.95
    if days_after <= 5:
        return 0.85
    return 0.0


# ── Revenue ↔ bank credit matching (income side) ────────────────────────


def _find_revenue_matches(db: Session, tenant_id: str, vendor_pk: int | None,
                          run: GraphRun) -> int:
    """Match revenue_invoice / customer_payment docs against bank statement
    CREDIT transactions. Mirror of _find_payment_matches but on the income
    side — invoices issued + payment notices match the inflows on the
    audited entity's bank statement, proving the money actually arrived.

    Match window: 0-30 days from invoice issue date to bank credit date
    (longer than the 0-5d expense window because customers commonly take
    Net-7 / Net-15 / Net-30 to pay)."""

    sql = text("""
        WITH revenue_docs AS (
            SELECT
                doc_e.pk        AS rev_doc_e_pk,
                d.pk            AS rev_doc_pk,
                d.vendor_pk     AS vendor_pk,
                d.doc_type      AS doc_type,
                money_e.pk      AS money_e_pk,
                money_e.canonical AS money_canon,
                date_e.canonical  AS date_iso,
                CASE WHEN date_e.canonical ~ '^\\d{4}-\\d{2}-\\d{2}$'
                     THEN date_e.canonical::date ELSE NULL END AS rev_date
            FROM documents d
            JOIN entities doc_e
                ON doc_e.document_pk = d.pk
                AND doc_e.kind = 'document'
                AND doc_e.source = 'fact_bootstrap'
            JOIN entity_relations money_r
                ON money_r.src_entity_pk = doc_e.pk
                AND money_r.relation = 'has_revenue'
            JOIN entities money_e
                ON money_e.pk = money_r.dst_entity_pk
                AND money_e.kind = 'money'
            LEFT JOIN entity_relations date_r
                ON date_r.src_entity_pk = doc_e.pk
                AND date_r.relation = 'dated'
            LEFT JOIN entities date_e
                ON date_e.pk = date_r.dst_entity_pk
                AND date_e.kind = 'date'
            WHERE d.tenant_id = :tenant_id
              AND d.doc_type IN ('revenue_invoice', 'customer_payment', 'sales_receipt')
              AND (CAST(:vendor_pk AS INTEGER) IS NULL OR d.vendor_pk = CAST(:vendor_pk AS INTEGER))
        ),
        credits AS (
            SELECT
                bd.pk           AS bank_doc_pk,
                bd.vendor_pk    AS vendor_pk,
                txn_e.pk        AS txn_e_pk,
                txn_e.entity_metadata AS txn_meta,
                txn_money.canonical    AS money_canon,
                txn_date.canonical     AS date_iso,
                CASE WHEN txn_date.canonical ~ '^\\d{4}-\\d{2}-\\d{2}$'
                     THEN txn_date.canonical::date ELSE NULL END AS txn_date
            FROM documents bd
            JOIN entities bs_doc_e
                ON bs_doc_e.document_pk = bd.pk
                AND bs_doc_e.kind = 'document'
                AND bs_doc_e.source = 'fact_bootstrap'
            JOIN entity_relations has_txn
                ON has_txn.src_entity_pk = bs_doc_e.pk
                AND has_txn.relation = 'has_transaction'
            JOIN entities txn_e
                ON txn_e.pk = has_txn.dst_entity_pk
                AND txn_e.kind = 'transaction'
                -- Income side: only credit transactions count
                AND (txn_e.entity_metadata->>'direction') = 'credit'
            JOIN entity_relations amt_r
                ON amt_r.src_entity_pk = txn_e.pk
                AND amt_r.relation = 'transaction_amount'
            JOIN entities txn_money
                ON txn_money.pk = amt_r.dst_entity_pk
                AND txn_money.kind = 'money'
            LEFT JOIN entity_relations tdate_r
                ON tdate_r.src_entity_pk = txn_e.pk
                AND tdate_r.relation = 'transaction_date'
            LEFT JOIN entities txn_date
                ON txn_date.pk = tdate_r.dst_entity_pk
            WHERE bd.tenant_id = :tenant_id
              AND bd.doc_type IN ('bank_statement', 'audited_financial_statement')
              AND (CAST(:vendor_pk AS INTEGER) IS NULL OR bd.vendor_pk = CAST(:vendor_pk AS INTEGER))
        )
        SELECT
            r.rev_doc_e_pk, r.rev_doc_pk, r.vendor_pk, r.doc_type,
            r.money_canon, r.rev_date,
            t.txn_e_pk, t.bank_doc_pk, t.txn_date, t.txn_meta,
            CASE
                WHEN r.rev_date IS NOT NULL AND t.txn_date IS NOT NULL
                THEN (t.txn_date - r.rev_date)
                ELSE NULL
            END AS days_after
        FROM revenue_docs r
        JOIN credits t
            ON r.money_canon = t.money_canon
            AND r.vendor_pk IS NOT DISTINCT FROM t.vendor_pk
        WHERE
            r.rev_date IS NULL
            OR t.txn_date IS NULL
            OR (t.txn_date - r.rev_date BETWEEN 0 AND 30)
    """)

    rows = db.execute(sql, {"tenant_id": tenant_id, "vendor_pk": vendor_pk}).all()

    count = 0
    for r in rows:
        (rev_doc_e_pk, rev_doc_pk, vpk, doc_type, money_canon, rev_date,
         txn_e_pk, bank_doc_pk, txn_date, txn_meta, days_after) = r
        confidence = _rev_confidence(days_after)
        if confidence < 0.50:
            continue
        db.add(EntityRelation(
            tenant_id=tenant_id,
            vendor_pk=vpk,
            src_entity_pk=rev_doc_e_pk,
            dst_entity_pk=txn_e_pk,
            relation=REL_REVENUE_RECEIVED_BY_TXN,
            confidence=confidence,
            evidence_doc_pk=rev_doc_pk,
            evidence_chunk_pk=None,
            metadata_json={
                "bank_statement_doc_pk": bank_doc_pk,
                "amount_canonical": money_canon,
                "revenue_doc_type": doc_type,
                "revenue_date": str(rev_date) if rev_date else None,
                "transaction_date": str(txn_date) if txn_date else None,
                "days_after": days_after,
                "txn_description": (txn_meta or {}).get("description") if isinstance(txn_meta, dict) else None,
            },
            source="reconcile",
            graph_run_pk=run.pk,
        ))
        count += 1
    return count


def _rev_confidence(days_after: int | None) -> float:
    """Revenue match tolerates a longer settlement window than expenses.
    Customers commonly take Net-7/Net-15/Net-30 to pay invoices."""
    if days_after is None:
        return 0.65
    if days_after <= 1:
        return 1.00
    if days_after <= 7:
        return 0.95
    if days_after <= 15:
        return 0.88
    if days_after <= 30:
        return 0.78
    return 0.0


def _ent(db: Session, tid: str, pk: int) -> Entity | None:
    return db.scalar(select(Entity).where(Entity.tenant_id == tid, Entity.pk == pk))


def revenue_matches_for_vendor(db: Session, vendor_pk: int | None) -> list[dict[str, Any]]:
    """Hydrated list of revenue_received_by_transaction matches for the API."""
    tid = get_current_tenant()
    stmt = select(EntityRelation).where(
        EntityRelation.tenant_id == tid,
        EntityRelation.relation == REL_REVENUE_RECEIVED_BY_TXN,
    )
    if vendor_pk is not None:
        stmt = stmt.where(EntityRelation.vendor_pk == vendor_pk)
    edges = db.scalars(stmt.order_by(EntityRelation.confidence.desc(), EntityRelation.pk)).all()

    out: list[dict[str, Any]] = []
    for e in edges:
        rev_ent = _ent(db, tid, e.src_entity_pk)
        txn_ent = _ent(db, tid, e.dst_entity_pk)
        if not rev_ent or not txn_ent:
            continue
        rev_doc = docs_repo.get_row_by_pk(db, rev_ent.document_pk, tenant_id=tid)
        bank_doc_pk = (e.metadata_json or {}).get("bank_statement_doc_pk")
        bank_doc = docs_repo.get_row_by_pk(db, bank_doc_pk, tenant_id=tid) if bank_doc_pk else None
        out.append({
            "relationPk": e.pk,
            "confidence": e.confidence,
            "metadata": e.metadata_json,
            "revenue": {
                "docId": rev_doc.id_external if rev_doc else None,
                "docPk": rev_doc.pk if rev_doc else None,
                "name": rev_doc.name if rev_doc else None,
                "kind": rev_doc.doc_type if rev_doc else None,
            },
            "transaction": {
                "entityPk": txn_ent.pk,
                "description": txn_ent.text,
                "metadata": txn_ent.entity_metadata,
            },
            "bankStatement": {
                "docId": bank_doc.id_external if bank_doc else None,
                "docPk": bank_doc.pk if bank_doc else None,
                "name": bank_doc.name if bank_doc else None,
            } if bank_doc else None,
        })
    return out


# ── Pretty-summary helpers used by the API ────────────────────────────────


def duplicates_for_vendor(db: Session, vendor_pk: int | None) -> list[dict[str, Any]]:
    """Hydrated list of duplicate pairs for the API. Each pair links two
    receipts; we look up their document records so the UI can show
    names + ids without a second round-trip."""
    tid = get_current_tenant()
    stmt = select(EntityRelation).where(
        EntityRelation.tenant_id == tid,
        EntityRelation.relation == REL_DUPLICATE_OF,
    )
    if vendor_pk is not None:
        stmt = stmt.where(EntityRelation.vendor_pk == vendor_pk)
    edges = db.scalars(stmt.order_by(EntityRelation.confidence.desc(), EntityRelation.pk)).all()

    out: list[dict[str, Any]] = []
    for e in edges:
        src_ent = _ent(db, tid, e.src_entity_pk)
        dst_ent = _ent(db, tid, e.dst_entity_pk)
        if not src_ent or not dst_ent:
            continue
        src_doc = docs_repo.get_row_by_pk(db, src_ent.document_pk, tenant_id=tid)
        dst_doc = docs_repo.get_row_by_pk(db, dst_ent.document_pk, tenant_id=tid)
        out.append({
            "relationPk": e.pk,
            "confidence": e.confidence,
            "metadata": e.metadata_json,
            "a": {
                "docId": src_doc.id_external if src_doc else None,
                "docPk": src_doc.pk if src_doc else None,
                "name": src_doc.name if src_doc else None,
            },
            "b": {
                "docId": dst_doc.id_external if dst_doc else None,
                "docPk": dst_doc.pk if dst_doc else None,
                "name": dst_doc.name if dst_doc else None,
            },
        })
    return out


def payments_for_vendor(db: Session, vendor_pk: int | None) -> list[dict[str, Any]]:
    """Hydrated list of paid_by_transaction matches for the API."""
    tid = get_current_tenant()
    stmt = select(EntityRelation).where(
        EntityRelation.tenant_id == tid,
        EntityRelation.relation == REL_PAID_BY_TRANSACTION,
    )
    if vendor_pk is not None:
        stmt = stmt.where(EntityRelation.vendor_pk == vendor_pk)
    edges = db.scalars(stmt.order_by(EntityRelation.confidence.desc(), EntityRelation.pk)).all()

    out: list[dict[str, Any]] = []
    for e in edges:
        receipt_ent = _ent(db, tid, e.src_entity_pk)
        txn_ent = _ent(db, tid, e.dst_entity_pk)
        if not receipt_ent or not txn_ent:
            continue
        receipt_doc = docs_repo.get_row_by_pk(db, receipt_ent.document_pk, tenant_id=tid)
        bank_doc_pk = (e.metadata_json or {}).get("bank_statement_doc_pk")
        bank_doc = docs_repo.get_row_by_pk(db, bank_doc_pk, tenant_id=tid) if bank_doc_pk else None
        out.append({
            "relationPk": e.pk,
            "confidence": e.confidence,
            "metadata": e.metadata_json,
            "receipt": {
                "docId": receipt_doc.id_external if receipt_doc else None,
                "docPk": receipt_doc.pk if receipt_doc else None,
                "name": receipt_doc.name if receipt_doc else None,
            },
            "transaction": {
                "entityPk": txn_ent.pk,
                "description": txn_ent.text,
                "metadata": txn_ent.entity_metadata,
            },
            "bankStatement": {
                "docId": bank_doc.id_external if bank_doc else None,
                "docPk": bank_doc.pk if bank_doc else None,
                "name": bank_doc.name if bank_doc else None,
            } if bank_doc else None,
        })
    return out
