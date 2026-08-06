"""Bootstrap the graph from Layer 1's `documents.extracted_fields`.

Idempotent: each call to `run(db, document_pk)` tears down the previous
bootstrap GraphRun for that doc and rebuilds. Safe to call after every
Re-extract, after each new ingest, or as part of a batch backfill.

Why bootstrap from existing facts before adding LLM-based extraction:
- ~60-80% of an audit document's interesting graph edges are already
  encoded in `extracted_fields` (parties, signatures, dates, totals,
  standards) — exactly the deterministic facts the schemas pin down.
- Zero new LLM cost.
- Gives us a working graph the same day Layer 3 schema lands, which lets
  reconciliation work (receipt ↔ bank statement on amount+date) without
  waiting for the entity extractor.
- Plays nicely with LLM-based extraction in L3.3: that pass *adds* free-
  text entities (names mentioned mid-clause, referenced standards in
  prose) but bootstrap already covers the structured spine.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.graph.canonical import (
    canon_date, canon_name, canon_name_sorted, canon_org, money_canonical,
    split_multi_person,
)
from app.orm import (
    Document, Entity, EntityRelation, GraphRun,
)
from app.repositories import documents as docs_repo

log = logging.getLogger("docaiq.graph.bootstrap")


# ── Entity kinds — pinned strings used throughout the graph layer ─────────

KIND_PERSON = "person"
KIND_ORG = "org"


def _levenshtein(a: str, b: str) -> int:
    """Minimal edit-distance · pure Python · O(len(a)*len(b)) time and
    O(min) space. Used by entity-linking to spot near-duplicates like
    'Smart Audit' vs 'Smart Audit Pte Ltd'."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    if len(a) > len(b):
        a, b = b, a
    prev = list(range(len(a) + 1))
    for i, cb in enumerate(b, 1):
        cur = [i] + [0] * len(a)
        for j, ca in enumerate(a, 1):
            cost = 0 if ca == cb else 1
            cur[j] = min(
                cur[j - 1] + 1,         # insertion
                prev[j] + 1,            # deletion
                prev[j - 1] + cost,     # substitution
            )
        prev = cur
    return prev[len(a)]
KIND_MONEY = "money"
KIND_DATE = "date"
KIND_LOCATION = "location"
KIND_STANDARD = "standard"
KIND_IDENTIFIER = "identifier"
KIND_DOCUMENT = "document"  # the doc itself is a graph node (self-loop hub)
KIND_TRANSACTION = "transaction"  # bank statement line items
KIND_CATEGORY = "category"  # expense category
KIND_EVENT = "event"  # Triangle of Attribution hub node (actor→event→target)


# ── Relation slugs — keep short + verb-style ──────────────────────────────

REL_PARTY_OF = "is_party_to"           # Person/Org → Document (this party signs/uses the doc)
REL_SIGNED_BY = "signed_by"            # Document → Person (who signed)
REL_EFFECTIVE_ON = "effective_on"      # Document → Date
REL_EXPIRES_ON = "expires_on"          # Document → Date
REL_DATED = "dated"                    # Document → Date (issue date)
REL_GOVERNED_BY = "governed_by"        # Document → Location (jurisdiction)
REL_HAS_TOTAL = "has_total"            # Document → Money
REL_PAID_BY = "paid_by"                # Receipt → Person (who paid)
REL_PAID_TO = "paid_to"                # Receipt → Org (who received)
REL_HOLDS = "holds"                    # Person → Document (passport/license holder)
REL_BORN_ON = "born_on"                # Person → Date (DOB)
REL_ISSUED_BY = "issued_by"            # Document → Org (issuing authority)
REL_CERTIFIES = "certifies"            # Document → Org (subject org of cert)
REL_REFERENCES_STANDARD = "references_standard"  # Document → Standard
REL_HAS_TRANSACTION = "has_transaction"  # BankStatement → Transaction
REL_TRANSACTION_AMOUNT = "transaction_amount"  # Transaction → Money
REL_TRANSACTION_DATE = "transaction_date"  # Transaction → Date
REL_OWNED_BY = "owned_by"              # BankStatement → Person/Org (account holder)
REL_CATEGORIZED_AS = "categorized_as"  # Receipt/Invoice → Category
# ── Income / revenue side ────────────────────────────────────────────────
REL_INVOICED_TO = "invoiced_to"          # RevenueInvoice → Customer (Org/Person)
REL_RECEIVED_FROM = "received_from"      # CustomerPayment → Payer
REL_SETTLES_INVOICE = "settles_invoice"  # CustomerPayment → RevenueInvoice (when ref matches)
REL_HAS_REVENUE = "has_revenue"          # RevenueInvoice → Money
REL_REVENUE_CATEGORY = "revenue_category"  # RevenueInvoice/Payment → Category


# ── Bootstrap entry point ─────────────────────────────────────────────────


def run(db: Session, document_pk: int) -> dict[str, int]:
    """Bootstrap graph entities + relations for one document. Idempotent —
    deletes any prior bootstrap run for this doc before rebuilding.

    Returns {entities_added, relations_added}. Caller commits.
    """
    doc = docs_repo.get_row_by_pk(db, document_pk)
    if doc is None:
        log.warning("bootstrap: doc pk=%s not found", document_pk)
        return {"entities_added": 0, "relations_added": 0}

    ef = doc.extracted_fields or {}
    fields = ef.get("fields") if isinstance(ef, dict) else None
    schema_key = ef.get("doc_type") if isinstance(ef, dict) else None
    if not fields:
        log.info("bootstrap: doc pk=%s has no extracted_fields; skipping", document_pk)
        return {"entities_added": 0, "relations_added": 0}

    # Tear down any prior bootstrap run for this doc — soft-deprecate
    # instead of CASCADE-delete so other docs that reference these entities
    # via alias edges aren't broken.
    prior_runs = db.scalars(
        select(GraphRun).where(
            GraphRun.document_pk == doc.pk,
            GraphRun.kind == "bootstrap",
        )
    ).all()
    now = datetime.utcnow()
    for r in prior_runs:
        # Mark old entities + relations as deprecated; they'll be filtered
        # out by queries that check `deprecated_at IS NULL`.
        db.execute(
            update(EntityRelation)
            .where(EntityRelation.graph_run_pk == r.pk)
            .values(deprecated_at=now)
        )
        db.execute(
            update(Entity)
            .where(Entity.graph_run_pk == r.pk)
            .values(deprecated_at=now)
        )
        # Mark the run as superseded (keep it for provenance).
        r.status = "superseded"
        r.completed_at = r.completed_at or now
    db.flush()

    run_row = GraphRun(
        tenant_id=doc.tenant_id,
        document_pk=doc.pk,
        kind="bootstrap",
        model=None,
        status="running",
    )
    db.add(run_row)
    db.flush()

    ctx = _BootstrapCtx(db=db, doc=doc, run=run_row)

    # Dispatch by schema key. Unknown schemas just create a single Document
    # node so the graph still has a hub for this doc.
    # Dedicated handler if one exists (richer), else the schema-agnostic generic handler — so
    # ANY type (incl. a newly-approved library schema) is graphed automatically, no new code.
    handler = _HANDLERS.get(schema_key) or _handle_generic
    if schema_key not in _HANDLERS:
        log.info("bootstrap: no dedicated handler for schema=%r on doc pk=%s → generic handler",
                 schema_key, doc.pk)
    try:
        handler(ctx, fields)
    except Exception as e:  # noqa: BLE001
        log.exception("bootstrap: handler %s failed for doc pk=%s: %s", schema_key, doc.pk, e)
        run_row.status = "failed"
        run_row.error = str(e)[:1000]
        run_row.completed_at = datetime.now(timezone.utc)
        db.flush()
        return {"entities_added": ctx.entities_added, "relations_added": ctx.relations_added}

    # Schema-agnostic reconciliation — runs AFTER the handler so it only fills
    # gaps (values the handler missed). Checks for existing entities (person OR
    # org) before creating, so a venue name the handler correctly classified as
    # org won't get a duplicate person entity from reconciliation.
    try:
        reconciled = _reconcile_fields(ctx, fields)
        if reconciled:
            log.info("bootstrap: reconciled %d entities for doc pk=%s", reconciled, doc.pk)
    except Exception:  # noqa: BLE001 — never block the main bootstrap
        log.exception("bootstrap: reconciliation failed for doc pk=%s — continuing", doc.pk)

    run_row.status = "complete"
    run_row.entities_added = ctx.entities_added
    run_row.relations_added = ctx.relations_added
    run_row.completed_at = datetime.now(timezone.utc)
    db.flush()

    log.info(
        "bootstrap: doc pk=%s schema=%s → %d entities, %d relations",
        doc.pk, schema_key, ctx.entities_added, ctx.relations_added,
    )
    return {"entities_added": ctx.entities_added, "relations_added": ctx.relations_added}


# ── Per-doc-type handlers ─────────────────────────────────────────────────

class _BootstrapCtx:
    """Per-document working state for the bootstrap pass. Caches the
    document hub-node so multiple handlers reuse it. Tracks counts for
    the run row's audit fields."""

    def __init__(self, db: Session, doc: Document, run: GraphRun):
        self.db = db
        self.doc = doc
        self.run = run
        self.tenant_id = doc.tenant_id
        self.vendor_pk = doc.vendor_pk
        self.entities_added = 0
        self.relations_added = 0
        self._doc_node: Entity | None = None

    def _new_entity(
        self,
        *,
        kind: str,
        text: str,
        canonical: str | None = None,
        metadata: dict[str, Any] | None = None,
        page: int = 1,
        confidence: float | None = None,
        source: str = "fact_bootstrap",
    ) -> Entity:
        # M44.P9.7 · semantic entity linking. Before creating a new
        # entity, try to match against an existing one in (tenant, kind)
        # with a similar canonical. Reuses the existing entity (just
        # appends an alias note) when a confident match is found ·
        # 'Smart Audit' and 'Smart Audit Pte Ltd' become one entity.
        # Only applied to person / org kinds where dedup matters.
        # Date / money / identifier dedup is meaningless (same value =
        # same entity already).
        if kind in (KIND_PERSON, KIND_ORG) and canonical:
            existing = self._find_alias(kind, canonical, text)
            if existing is not None:
                self._note_alias_on_entity(existing, text)
                return existing

        e = Entity(
            tenant_id=self.tenant_id,
            document_pk=self.doc.pk,
            chunk_pk=None,
            vendor_pk=self.vendor_pk,
            kind=kind,
            text=text[:512],
            canonical=(canonical or "")[:256] or None,
            page=page,
            entity_metadata=metadata,
            source=source,
            graph_run_pk=self.run.pk,
            confidence=confidence,
        )
        self.db.add(e)
        self.db.flush()
        self.entities_added += 1
        return e

    # M44.P9.7 · helpers ────────────────────────────────────────────────
    def _find_alias(self, kind: str, canonical: str, raw_text: str) -> Entity | None:
        """Look for an existing entity in this tenant+kind that's
        semantically the same as the one we're about to create. Four
        signals (cheapest first):
          1. Exact canonical match
          2. Token-sorted match (person only — catches old unsorted entities)
          3. Substring containment ('smart audit' ⊂ 'smart audit pte ltd')
          4. Levenshtein distance < 3 between canonicals
        """
        from sqlalchemy import select as _select
        from app.orm import Entity as _E

        rows = self.db.scalars(
            _select(_E).where(
                _E.tenant_id == self.tenant_id,
                _E.kind == kind,
                _E.canonical.is_not(None),
                _E.deprecated_at.is_(None),
            ).limit(200)
        ).all()

        cn = canonical.strip().lower()
        if not cn:
            return None

        # 1. Exact canonical match
        for r in rows:
            rc = (r.canonical or "").strip().lower()
            if rc and rc == cn:
                return r

        # 2. Token-sorted match — catches old person entities whose
        #    canonical was stored before canon_name_sorted() was
        #    introduced.  "goda rajesh" (sorted new) ↔ "rajesh goda"
        #    (unsorted old) both sort to "goda rajesh".
        if kind == KIND_PERSON:
            cn_sorted = " ".join(sorted(cn.split()))
            for r in rows:
                rc = (r.canonical or "").strip().lower()
                if rc and " ".join(sorted(rc.split())) == cn_sorted:
                    return r

        # 3. Substring (the longer one is the "rich" name; we want either)
        for r in rows:
            rc = (r.canonical or "").strip().lower()
            if not rc or len(rc) < 4 or len(cn) < 4:
                continue
            # short string fully contained in the other (must be ≥ 60%
            # of the longer one to avoid 'al' matching 'Walmart')
            short, long = (cn, rc) if len(cn) < len(rc) else (rc, cn)
            # 55% length-ratio · catches 'Smart Audit' (11) ⊂
            # 'Smart Audit Pte Ltd' (19) at 0.58, while still
            # rejecting overly-loose matches like 'al' ⊂ 'walmart'.
            if short in long and len(short) / max(len(long), 1) >= 0.55:
                return r

        # 4. Levenshtein on short canonicals
        for r in rows:
            rc = (r.canonical or "").strip().lower()
            if not rc:
                continue
            if abs(len(rc) - len(cn)) > 4:
                continue
            if _levenshtein(rc, cn) <= 3:
                return r

        return None

    def _note_alias_on_entity(self, entity: Entity, raw_alias: str) -> None:
        """Track the alternate spelling on the kept entity's metadata
        so the UI can show 'also seen as'. Doesn't create a new
        entity, doesn't bump entities_added."""
        meta = dict(entity.entity_metadata or {})
        aliases = list(meta.get("aliases") or [])
        if raw_alias and raw_alias not in aliases:
            aliases.append(raw_alias)
            meta["aliases"] = aliases[:8]
            entity.entity_metadata = meta
            from sqlalchemy.orm.attributes import flag_modified
            flag_modified(entity, "entity_metadata")

    def doc_node(self) -> Entity:
        """The document itself as a graph hub. Memoized per run."""
        if self._doc_node is None:
            self._doc_node = self._new_entity(
                kind=KIND_DOCUMENT,
                text=self.doc.name or self.doc.id_external,
                canonical=self.doc.id_external,
                metadata={
                    "doc_id": self.doc.id_external,
                    "doc_type": self.doc.doc_type,
                    "doc_type_confidence": self.doc.doc_type_confidence,
                },
            )
        return self._doc_node

    def person(self, name: str, *, page: int = 1, extra: dict | None = None) -> Entity | None:
        # Token-sorted canonical so word-order variants collapse to the same
        # key: "Rajesh Goda" and "Goda Rajesh" both → "goda rajesh".  Only
        # for person names — org/location canonicals keep original order.
        c = canon_name_sorted(name)
        if not c:
            return None
        return self._new_entity(
            kind=KIND_PERSON,
            text=name,
            canonical=c,
            page=page,
            metadata=extra or None,
        )

    def org(self, name: str, *, page: int = 1, extra: dict | None = None) -> Entity | None:
        c = canon_org(name)
        if not c:
            return None
        return self._new_entity(
            kind=KIND_ORG,
            text=name,
            canonical=c,
            page=page,
            metadata=extra or None,
        )

    def money(self, raw: str, *, page: int = 1, extra: dict | None = None) -> Entity | None:
        c = money_canonical(raw)
        if not c:
            return None
        return self._new_entity(
            kind=KIND_MONEY,
            text=raw,
            canonical=c,
            page=page,
            metadata=extra or None,
        )

    def date(self, raw: str, *, page: int = 1, role: str | None = None) -> Entity | None:
        c = canon_date(raw)
        if not c:
            return None
        return self._new_entity(
            kind=KIND_DATE,
            text=raw,
            canonical=c,
            page=page,
            metadata={"iso": c, "role": role} if role else {"iso": c},
        )

    def location(self, name: str, *, page: int = 1) -> Entity | None:
        c = canon_org(name)
        if not c:
            return None
        return self._new_entity(
            kind=KIND_LOCATION,
            text=name,
            canonical=c,
            page=page,
        )

    def standard(self, name: str, *, page: int = 1) -> Entity | None:
        c = canon_name(name)
        if not c:
            return None
        return self._new_entity(
            kind=KIND_STANDARD,
            text=name,
            canonical=c,
            page=page,
        )

    def identifier(self, raw: str, *, kind_tag: str, page: int = 1) -> Entity | None:
        if not raw:
            return None
        return self._new_entity(
            kind=KIND_IDENTIFIER,
            text=raw,
            canonical=raw.lower(),
            page=page,
            metadata={"id_kind": kind_tag},
        )

    def category(self, label: str) -> Entity | None:
        c = canon_name(label)
        if not c:
            return None
        return self._new_entity(
            kind=KIND_CATEGORY,
            text=label,
            canonical=c,
        )

    def transaction(self, *, description: str, amount: str, date_str: str | None, direction: str | None,
                    page: int = 1) -> Entity | None:
        if not description:
            return None
        amt, cur = (None, None)
        if amount:
            from app.graph.canonical import canon_money
            amt, cur = canon_money(amount)
        return self._new_entity(
            kind=KIND_TRANSACTION,
            text=description,
            canonical=f"txn::{amt or '?'}::{(date_str or '').strip()[:10]}",
            page=page,
            metadata={
                "amount_raw": amount,
                "amount": amt,
                "currency": cur,
                "date": canon_date(date_str) if date_str else None,
                "direction": direction,
            },
        )

    def link(self, src: Entity | None, rel: str, dst: Entity | None, *, confidence: float | None = None,
             metadata: dict | None = None, page: int | None = None) -> None:
        if src is None or dst is None:
            return
        e = EntityRelation(
            tenant_id=self.tenant_id,
            vendor_pk=self.vendor_pk,
            src_entity_pk=src.pk,
            dst_entity_pk=dst.pk,
            relation=rel,
            confidence=confidence,
            evidence_doc_pk=self.doc.pk,
            evidence_chunk_pk=None,
            metadata_json=metadata,
            source="fact_bootstrap",
            graph_run_pk=self.run.pk,
        )
        self.db.add(e)
        self.relations_added += 1

    def add_event(
        self, action: str, actor: Entity | None, target: Entity | None,
        *, date_str: str | None = None, page: int = 1, confidence: float | None = None,
    ) -> Entity | None:
        """Triangle of Attribution: create an Event hub node and link
        (actor)-[:actor]→(event)-[:target]→(target). Optionally link a
        date entity via :context edge. Returns the event entity, or None
        if actor or target is missing."""
        if actor is None or target is None:
            return None
        label = f"{action}"
        if date_str:
            label += f" on {date_str}"
        event = self._new_entity(
            kind=KIND_EVENT,
            text=label,
            canonical=label.lower(),
            metadata={"action": action, "timestamp": date_str},
            page=page,
            confidence=confidence,
        )
        self.link(actor, "actor", event, confidence=confidence)
        self.link(event, "target", target, confidence=confidence)
        return event


def _handle_agreement(ctx: _BootstrapCtx, fields: dict) -> None:
    doc_node = ctx.doc_node()

    # Parties
    for p in (fields.get("parties") or []):
        name = (p or {}).get("name") if isinstance(p, dict) else None
        role = (p or {}).get("role") if isinstance(p, dict) else None
        if not name:
            continue
        # Heuristic: ALL-CAPS legal names that look like companies → org.
        is_org = name.isupper() or any(suffix in name.upper() for suffix in (" PTE", " LTD", " INC", " LLC", " GMBH", " CORP"))
        ent = ctx.org(name, extra={"role": role}) if is_org else ctx.person(name, extra={"role": role})
        ctx.link(ent, REL_PARTY_OF, doc_node, metadata={"role": role})

    # Signature blocks — event-centric: (signer)-[:actor]→(Event: signed)-[:target]→(doc)
    for sb in (fields.get("signature_blocks") or []):
        if not isinstance(sb, dict):
            continue
        signer = ctx.person(sb.get("signatory_name", ""), page=sb.get("page") or 1,
                            extra={"role": sb.get("signatory_role")})
        ctx.link(doc_node, REL_SIGNED_BY, signer,
                 metadata={"date": sb.get("signature_date"), "page": sb.get("page")})
        ctx.add_event("signed", signer, doc_node,
                      date_str=sb.get("signature_date"),
                      page=sb.get("page") or 1)

    # Dates
    eff = ctx.date(fields.get("effective_date"), role="effective")
    ctx.link(doc_node, REL_EFFECTIVE_ON, eff)
    exp = ctx.date(fields.get("expiry_date"), role="expiry")
    ctx.link(doc_node, REL_EXPIRES_ON, exp)

    # Jurisdiction
    juris = ctx.location(fields.get("jurisdiction", ""))
    ctx.link(doc_node, REL_GOVERNED_BY, juris)

    # Total value
    tv = fields.get("total_value")
    if tv:
        money = ctx.money(tv)
        ctx.link(doc_node, REL_HAS_TOTAL, money)


def _handle_receipt(ctx: _BootstrapCtx, fields: dict) -> None:
    doc_node = ctx.doc_node()

    vendor = ctx.org(fields.get("vendor_name", ""))
    claimant = ctx.person(fields.get("customer_or_claimant", ""))
    ctx.link(doc_node, REL_PAID_BY, claimant)
    ctx.link(doc_node, REL_PAID_TO, vendor)

    d = ctx.date(fields.get("date"), role="receipt")
    ctx.link(doc_node, REL_DATED, d)

    total_raw = fields.get("total")
    if total_raw:
        currency = fields.get("currency")
        full = f"{total_raw} {currency}" if currency and currency not in str(total_raw) else str(total_raw)
        money = ctx.money(full)
        ctx.link(doc_node, REL_HAS_TOTAL, money)

    cat = fields.get("category")
    if cat:
        cat_ent = ctx.category(cat)
        ctx.link(doc_node, REL_CATEGORIZED_AS, cat_ent)


def _handle_invoice(ctx: _BootstrapCtx, fields: dict) -> None:
    doc_node = ctx.doc_node()

    vendor = (fields.get("vendor") or {})
    customer = (fields.get("customer") or {})
    if isinstance(vendor, dict) and vendor.get("name"):
        v_ent = ctx.org(vendor["name"], extra={"address": vendor.get("address"), "tax_id": vendor.get("tax_id")})
        ctx.link(doc_node, REL_PAID_TO, v_ent)
        # Event: payment to vendor
        ctx.add_event("paid", doc_node, v_ent,
                      date_str=fields.get("issue_date"),
                      confidence=0.85)
    if isinstance(customer, dict) and customer.get("name"):
        c_ent = ctx.org(customer["name"], extra={"address": customer.get("address")})
        ctx.link(doc_node, REL_PAID_BY, c_ent)
        # Event: payment from customer
        ctx.add_event("paid", c_ent, doc_node,
                      date_str=fields.get("issue_date"),
                      confidence=0.85)

    issue = ctx.date(fields.get("issue_date"), role="issue")
    ctx.link(doc_node, REL_DATED, issue)
    due = ctx.date(fields.get("due_date"), role="due")
    ctx.link(doc_node, REL_EXPIRES_ON, due, metadata={"role": "due"})

    total = fields.get("total")
    if total:
        currency = fields.get("currency")
        full = f"{total} {currency}" if currency and currency not in str(total) else str(total)
        money = ctx.money(full)
        ctx.link(doc_node, REL_HAS_TOTAL, money)


def _handle_bank_statement(ctx: _BootstrapCtx, fields: dict) -> None:
    doc_node = ctx.doc_node()

    bank = ctx.org(fields.get("bank_or_org_name", ""))
    ctx.link(doc_node, REL_ISSUED_BY, bank)

    holder = fields.get("account_holder", "")
    if holder:
        # Could be a person or an org; default to person.
        holder_ent = ctx.person(holder)
        ctx.link(doc_node, REL_OWNED_BY, holder_ent)

    p_start = ctx.date(fields.get("statement_period_start"), role="period_start")
    p_end = ctx.date(fields.get("statement_period_end"), role="period_end")
    ctx.link(doc_node, REL_EFFECTIVE_ON, p_start, metadata={"role": "period_start"})
    ctx.link(doc_node, REL_EXPIRES_ON, p_end, metadata={"role": "period_end"})

    # Closing balance
    close = fields.get("closing_balance")
    if close:
        currency = fields.get("currency")
        full = f"{close} {currency}" if currency and currency not in str(close) else str(close)
        money = ctx.money(full)
        ctx.link(doc_node, REL_HAS_TOTAL, money, metadata={"role": "closing_balance"})

    # Transactions — each one becomes its own Transaction node with edges
    # to its amount and date. This is the layer reconciliation queries
    # against (match receipt.has_total ↔ transaction.transaction_amount).
    for txn in (fields.get("top_transactions") or []):
        if not isinstance(txn, dict):
            continue
        currency = fields.get("currency")
        amount_raw = txn.get("amount", "")
        full_amount = f"{amount_raw} {currency}" if amount_raw and currency and currency not in str(amount_raw) else str(amount_raw)
        t_ent = ctx.transaction(
            description=txn.get("description", ""),
            amount=full_amount,
            date_str=txn.get("date"),
            direction=txn.get("direction"),
        )
        ctx.link(doc_node, REL_HAS_TRANSACTION, t_ent)
        if t_ent and amount_raw:
            money = ctx.money(full_amount)
            ctx.link(t_ent, REL_TRANSACTION_AMOUNT, money)
        if t_ent and txn.get("date"):
            d_ent = ctx.date(txn.get("date"), role="transaction")
            ctx.link(t_ent, REL_TRANSACTION_DATE, d_ent)


def _handle_id_document(ctx: _BootstrapCtx, fields: dict) -> None:
    doc_node = ctx.doc_node()

    # holder_name with fallback: full_name, given_names + family_name (KYC
    # split-name pattern), then bare holder_name. Some extractions return
    # full_name instead of holder_name (e.g. classifier label "passport"
    # may route through a different schema path). Include race + sex +
    # nationality on the person entity.
    full_name = " ".join(
        n for n in [fields.get("given_names"), fields.get("family_name"),
                    fields.get("holder_name"), fields.get("full_name")]
        if n
    ).strip()
    holder = ctx.person(full_name or fields.get("holder_name", ""),
                        extra={"sex": fields.get("sex"), "nationality": fields.get("nationality"),
                               "race": fields.get("race")})
    ctx.link(holder, REL_HOLDS, doc_node)

    # issuing country — fallback to country_code (KYC pattern)
    country = ctx.location(fields.get("issuing_country") or fields.get("issuing_country_code", ""))
    ctx.link(doc_node, REL_GOVERNED_BY, country)

    # issuing authority — fallback to issuing_state/province (KYC pattern)
    authority = ctx.org(fields.get("issuing_authority") or fields.get("issuing_state")
                        or fields.get("issuing_province", ""))
    ctx.link(doc_node, REL_ISSUED_BY, authority)

    # dates with fallbacks (matching _handle_kyc_id pattern)
    issue = ctx.date(fields.get("date_of_issue") or fields.get("issue_date"), role="issue")
    ctx.link(doc_node, REL_DATED, issue)
    expiry = ctx.date(fields.get("date_of_expiry") or fields.get("expiry_date"), role="expiry")
    ctx.link(doc_node, REL_EXPIRES_ON, expiry)
    dob = ctx.date(fields.get("date_of_birth") or fields.get("dob"), role="dob")
    ctx.link(holder, REL_BORN_ON, dob)

    # place_of_birth → location linked to the holder
    pob = (fields.get("place_of_birth") or "").strip()
    if pob:
        ctx.link(holder, REL_GOVERNED_BY, ctx.location(pob))

    # document_number → identifier
    doc_no = fields.get("document_number")
    if doc_no:
        ident = ctx.identifier(doc_no, kind_tag=fields.get("doc_subtype") or "doc_no")
        ctx.link(doc_node, "has_identifier", ident)

    # national_id_number → secondary identifier (e.g. NRIC under passport)
    nat_id = fields.get("national_id_number")
    if nat_id:
        ident = ctx.identifier(nat_id, kind_tag="national_id")
        ctx.link(doc_node, "has_identifier", ident)


def _handle_certificate(ctx: _BootstrapCtx, fields: dict) -> None:
    doc_node = ctx.doc_node()

    auth = ctx.org(fields.get("issuing_authority", ""))
    ctx.link(doc_node, REL_ISSUED_BY, auth)

    subject = ctx.org(fields.get("subject_org", ""))
    ctx.link(doc_node, REL_CERTIFIES, subject)

    issue = ctx.date(fields.get("issue_date"), role="issue")
    ctx.link(doc_node, REL_DATED, issue)
    expiry = ctx.date(fields.get("expiry_date"), role="expiry")
    ctx.link(doc_node, REL_EXPIRES_ON, expiry)

    for st in (fields.get("standards_covered") or []):
        if not st:
            continue
        s_ent = ctx.standard(str(st))
        ctx.link(doc_node, REL_REFERENCES_STANDARD, s_ent)


def _handle_policy(ctx: _BootstrapCtx, fields: dict) -> None:
    doc_node = ctx.doc_node()

    owner = ctx.person(fields.get("owner", ""))
    ctx.link(doc_node, REL_OWNED_BY, owner)

    eff = ctx.date(fields.get("effective_date"), role="effective")
    ctx.link(doc_node, REL_EFFECTIVE_ON, eff)
    rev = ctx.date(fields.get("last_reviewed_date"), role="last_reviewed")
    ctx.link(doc_node, REL_DATED, rev)
    next_rev = ctx.date(fields.get("next_review_date"), role="next_review")
    ctx.link(doc_node, REL_EXPIRES_ON, next_rev)

    for st in (fields.get("related_standards") or []):
        if not st:
            continue
        s_ent = ctx.standard(str(st))
        ctx.link(doc_node, REL_REFERENCES_STANDARD, s_ent)


def _handle_kyc_id(ctx: _BootstrapCtx, fields: dict) -> None:
    """KYC vision-extractor blob (schemas in app/agents/kyc_extractor.py).
    Same semantic graph as id_document, but the field-name spelling differs
    because the KYC extractor predates the fact_extractor. Map them through."""
    doc_node = ctx.doc_node()

    # Combine family + given names (KYC splits them; fact_extractor doesn't)
    full_name = " ".join(
        n for n in [fields.get("given_names"), fields.get("family_name"), fields.get("holder_name")]
        if n
    ).strip()
    if full_name:
        holder = ctx.person(full_name, extra={
            "sex": fields.get("sex"),
            "nationality": fields.get("nationality"),
        })
        ctx.link(holder, REL_HOLDS, doc_node)
        # Birth date attaches to the person, not the doc.
        dob = ctx.date(fields.get("date_of_birth") or fields.get("dob"), role="dob")
        ctx.link(holder, REL_BORN_ON, dob)

    # Country / issuing authority
    country_code = fields.get("doc_country") or fields.get("issuing_country") or fields.get("issuing_country_code")
    if country_code:
        country = ctx.location(country_code)
        ctx.link(doc_node, REL_GOVERNED_BY, country)

    authority = fields.get("issuing_authority") or fields.get("issuing_state") or fields.get("issuing_province")
    if authority:
        auth_ent = ctx.org(authority)
        ctx.link(doc_node, REL_ISSUED_BY, auth_ent)

    # Dates
    issue = ctx.date(fields.get("date_of_issue") or fields.get("issue_date"), role="issue")
    ctx.link(doc_node, REL_DATED, issue)
    expiry = ctx.date(fields.get("date_of_expiry") or fields.get("expiry_date"), role="expiry")
    ctx.link(doc_node, REL_EXPIRES_ON, expiry)

    # Document number(s) — the primary ID + secondary national id if present.
    for fname, kind_tag in [
        ("doc_number", "passport_no"),
        ("document_number", "doc_no"),
        ("national_id_number", "national_id"),
        ("aadhaar_number_last_4", "aadhaar_last4"),
        ("pan_number", "pan"),
        ("nric_last_4", "nric_last4"),
        ("id_number_last_4", "id_last4"),
        ("cpf_number", "cpf"),
        ("rg_number", "rg"),
    ]:
        v = fields.get(fname)
        if v:
            ident = ctx.identifier(str(v), kind_tag=kind_tag)
            ctx.link(doc_node, "has_identifier", ident)


def _handle_revenue_invoice(ctx: _BootstrapCtx, fields: dict) -> None:
    """Income side — invoice issued BY the audited entity TO a customer.
    Creates Customer (Org) node, customer↔doc edge, revenue Money node,
    invoice issue/due dates, and a revenue_category tag node."""
    doc_node = ctx.doc_node()

    seller = (fields.get("seller") or {})
    if isinstance(seller, dict) and seller.get("name"):
        s_ent = ctx.org(seller["name"], extra={
            "address": seller.get("address"), "tax_id": seller.get("tax_id"),
            "role": "seller",
        })
        ctx.link(doc_node, REL_ISSUED_BY, s_ent)

    customer = (fields.get("customer") or {})
    if isinstance(customer, dict) and customer.get("name"):
        c_ent = ctx.org(customer["name"], extra={
            "address": customer.get("address"),
            "customer_id": customer.get("customer_id"),
            "role": "customer",
        })
        ctx.link(doc_node, REL_INVOICED_TO, c_ent)

    issue = ctx.date(fields.get("issue_date"), role="issue")
    ctx.link(doc_node, REL_DATED, issue)
    due = ctx.date(fields.get("due_date"), role="due")
    ctx.link(doc_node, REL_EXPIRES_ON, due, metadata={"role": "due"})

    total = fields.get("total")
    if total:
        currency = fields.get("currency")
        full = f"{total} {currency}" if currency and currency not in str(total) else str(total)
        money = ctx.money(full, extra={"side": "revenue"})
        ctx.link(doc_node, REL_HAS_REVENUE, money)

    cat = fields.get("revenue_category")
    if cat:
        c_ent = ctx.category(cat)
        ctx.link(doc_node, REL_REVENUE_CATEGORY, c_ent)


def _handle_customer_payment(ctx: _BootstrapCtx, fields: dict) -> None:
    """Income side — incoming money. Created from a payment notification /
    settlement notice / FAST/PayNow advice. Links the payer + the amount,
    and (when present) the invoice the payment settles."""
    doc_node = ctx.doc_node()

    payer = fields.get("payer_name", "")
    if payer:
        p_ent = ctx.org(payer, extra={"role": "payer"})
        ctx.link(doc_node, REL_RECEIVED_FROM, p_ent)

    d = ctx.date(fields.get("payment_date"), role="payment")
    ctx.link(doc_node, REL_DATED, d)

    amount = fields.get("amount")
    if amount:
        currency = fields.get("currency")
        full = f"{amount} {currency}" if currency and currency not in str(amount) else str(amount)
        money = ctx.money(full, extra={"side": "revenue", "method": fields.get("method")})
        ctx.link(doc_node, REL_HAS_REVENUE, money)

    inv_ref = fields.get("against_invoice_number")
    if inv_ref:
        # Soft-pointer node — actual cross-doc settles_invoice resolution
        # happens in the reconcile pass when we have the matching invoice.
        ref_ent = ctx.identifier(inv_ref, kind_tag="invoice_ref")
        ctx.link(doc_node, REL_SETTLES_INVOICE, ref_ent)

    cat = fields.get("revenue_category")
    if cat:
        c_ent = ctx.category(cat)
        ctx.link(doc_node, REL_REVENUE_CATEGORY, c_ent)


# ── Insurance certificate ──────────────────────────────────────────────────

def _handle_insurance_certificate(ctx: _BootstrapCtx, fields: dict) -> None:
    doc_node = ctx.doc_node()

    # insurer → org that issued the certificate
    insurer = ctx.org(fields.get("insurer_name", ""))
    ctx.link(doc_node, REL_ISSUED_BY, insurer)

    # policyholder → person or org that holds the policy
    policyholder = (fields.get("policyholder_name") or "").strip()
    if policyholder:
        ent = ctx.org(policyholder) if _looks_like_org(policyholder) else ctx.person(policyholder)
        ctx.link(ent, REL_HOLDS, doc_node)

    # effective / expiry dates
    eff = ctx.date(fields.get("effective_date"), role="effective")
    ctx.link(doc_node, REL_EFFECTIVE_ON, eff)
    exp = ctx.date(fields.get("expiry_date"), role="expiry")
    ctx.link(doc_node, REL_EXPIRES_ON, exp)

    # sum insured + premium → money
    for money_field, role in [("sum_insured", "sum_insured"), ("premium", "premium")]:
        val = fields.get(money_field)
        if val:
            ctx.link(doc_node, REL_HAS_TOTAL, ctx.money(val, extra={"role": role}))

    # policy number → identifier
    policy_no = fields.get("policy_number")
    if policy_no:
        ident = ctx.identifier(policy_no, kind_tag="policy_number")
        ctx.link(doc_node, "has_identifier", ident)

    # vehicle registration → identifier
    vehicle_reg = fields.get("vehicle_registration")
    if vehicle_reg:
        ident = ctx.identifier(vehicle_reg, kind_tag="vehicle_registration")
        ctx.link(doc_node, "has_identifier", ident)

    # governing law → location
    gov_law = (fields.get("governing_law") or "").strip()
    if gov_law:
        ctx.link(doc_node, REL_GOVERNED_BY, ctx.location(gov_law))

    # agent / broker → party
    agent = (fields.get("agent_or_broker") or "").strip()
    if agent:
        ent = ctx.org(agent) if _looks_like_org(agent) else ctx.person(agent, extra={"role": "agent_or_broker"})
        ctx.link(ent, REL_PARTY_OF, doc_node, metadata={"role": "agent_or_broker"})


# ── Business profile (ACRA / Companies House / Sec-of-State) ───────────────

def _handle_business_profile(ctx: _BootstrapCtx, fields: dict) -> None:
    doc_node = ctx.doc_node()

    # entity_name / business_name / name — the org this profile is about.
    # Universal-schema extractions (documents product) use business_name or
    # bare name; curated-schema extractions use entity_name.
    entity_name = (fields.get("entity_name") or fields.get("business_name")
                   or fields.get("name") or "").strip()
    # Filter out obviously non-business names (e.g. "KALYANI GODA RAJESH" in
    # an owners array could bleed into a top-level name field).
    if entity_name and _looks_like_org(entity_name):
        subject_ent = ctx.org(entity_name)
        ctx.link(doc_node, REL_CERTIFIES, subject_ent)

    # registration / identification number → identifier
    reg_no = fields.get("registration_number") or fields.get("identification_number")
    if reg_no:
        # Clean up universal-schema prefix like "Identification number\n..."
        clean = reg_no.split("\n")[-1].strip() if "\n" in str(reg_no) else str(reg_no).strip()
        ident = ctx.identifier(clean, kind_tag="registration")
        ctx.link(doc_node, "has_identifier", ident)

    # registration date
    reg_date = ctx.date(fields.get("registration_date"), role="registration")
    ctx.link(doc_node, REL_DATED, reg_date)

    # jurisdiction → location
    jurisdiction = (fields.get("jurisdiction") or "").strip()
    if jurisdiction:
        ctx.link(doc_node, REL_GOVERNED_BY, ctx.location(jurisdiction))

    # People arrays — officers[] (curated) or owners[] / shareholders[] /
    # individual_registrable_controllers[] (universal). Each entry may have
    # "name" (curated) or "value" + "label" (universal envelope).
    for array_key in ("officers", "owners", "shareholders", "individual_registrable_controllers"):
        for entry in (fields.get(array_key) or []):
            if not isinstance(entry, dict):
                continue
            name = (entry.get("name") or entry.get("value") or "").strip()
            if name:
                role = entry.get("role") or entry.get("label") or array_key.rstrip("s")
                person = ctx.person(name, extra={"role": role})
                ctx.link(person, REL_PARTY_OF, doc_node, metadata={"role": role})


# ── Résumé / CV ────────────────────────────────────────────────────────────

def _handle_resume(ctx: _BootstrapCtx, fields: dict) -> None:
    doc_node = ctx.doc_node()

    # full_name → person (candidate)
    full_name = (fields.get("full_name") or "").strip()
    person = ctx.person(full_name) if full_name else None
    if person:
        ctx.link(person, REL_PARTY_OF, doc_node, metadata={"role": "candidate"})

    # experience[] → org entities linked to the person
    for exp in (fields.get("experience") or []):
        if not isinstance(exp, dict):
            continue
        org_name = (exp.get("organization") or exp.get("organisation") or "").strip()
        if org_name:
            org = ctx.org(org_name)
            title = exp.get("title", "")
            if person:
                ctx.link(person, REL_PARTY_OF, org,
                         metadata={"role": "employee", "title": title} if title else {"role": "employee"})


# ── Universal extractor handler (Move-1 PR2) ──────────────────────────────
# The documents product ALWAYS extracts with the universal-adaptive schema
# (fact_extractor.py), so its extracted_fields.doc_type == "universal". Without a
# handler here, universal docs fell to the else-branch in run() and produced only
# a bare Document node — zero parties/dates/amounts/identifiers in the graph, so
# /graph/traverse + reconciliation saw nothing on the DEFAULT extraction path.
# This maps the generic universal slots to graph entities/relations. It
# complements the free-text llm_ner pass (agents/ner_extractor): bootstrap is the
# cheap deterministic spine from the STRUCTURED extraction; NER adds prose entities.

_ORG_TOKENS = (" PTE", " LTD", " LIMITED", " INC", " LLC", " GMBH", " CORP",
               " PLC", " COMPANY", " BANK", " GROUP", " HOLDINGS",
               " AG", " SA", " BV", " NV")


def _looks_like_org(name: str) -> bool:
    """Cheap person-vs-org heuristic: corporate suffix → org; else person.

    Does NOT use ALL-CAPS as a signal — bank statements, KYC documents, and
    legal forms routinely format person names in ALL CAPS, so ``isupper()``
    alone would misclassify "KALYANI GODA" as an org (entity-query divergence
    fix, 2026-07-27).

    Tokens are space-prefixed (e.g. " LTD") to match at word boundaries, so
    we prepend a space to the name so first-word matches (e.g. "Bank") work."""
    if not name:
        return False
    upper = " " + name.upper()
    return any(tok in upper for tok in _ORG_TOKENS)


def _universal_date_rel(label: str) -> str:
    """Pick a graph relation for a labeled universal date so expiries/effective
    dates land on the same edges alerts + reconciliation already use."""
    lab = (label or "").lower()
    if any(t in lab for t in ("expir", "valid_until", "valid_to", "end_date", "maturity", "renewal")):
        return REL_EXPIRES_ON
    if any(t in lab for t in ("effective", "commence", "issue", "start")):
        return REL_EFFECTIVE_ON
    return REL_DATED


def _handle_universal(ctx: _BootstrapCtx, fields: dict) -> None:
    doc_node = ctx.doc_node()

    # Enrich the hub with the extractor's precise self-labeled type.
    dt = (fields.get("detected_doc_type") or "").strip()
    if dt:
        from sqlalchemy.orm.attributes import flag_modified
        meta = dict(doc_node.entity_metadata or {})
        meta["detected_doc_type"] = dt
        if fields.get("detected_doc_subtype"):
            meta["detected_doc_subtype"] = fields.get("detected_doc_subtype")
        doc_node.entity_metadata = meta
        flag_modified(doc_node, "entity_metadata")

    # Issuer — split multi-person/joint-holder values so each person or org
    # gets their own Entity row and link.
    issuer = (fields.get("issuer") or "").strip()
    if issuer:
        issuer_names = split_multi_person(issuer)
        for iname in issuer_names:
            is_split = len(issuer_names) > 1
            is_org = (not is_split) and _looks_like_org(iname)
            ent = ctx.org(iname, extra={"address": fields.get("issuer_address") or None}) if is_org \
                else ctx.person(iname, extra={"address": fields.get("issuer_address") or None})
            ctx.link(doc_node, REL_ISSUED_BY, ent)

    # Subject / recipient — the person or org the doc is about.
    subj = (fields.get("subject_or_recipient") or "").strip()
    if subj:
        subj_names = split_multi_person(subj)
        for sname in subj_names:
            is_split = len(subj_names) > 1
            is_org = (not is_split) and _looks_like_org(sname)
            ent = ctx.org(sname) if is_org else ctx.person(sname)
            ctx.link(ent, REL_PARTY_OF, doc_node, metadata={"role": "subject_or_recipient"})

    # Other named parties — split each name for co-applicants / joint holders.
    for p in (fields.get("parties") or []):
        if not isinstance(p, dict):
            continue
        name = (p.get("name") or "").strip()
        if not name:
            continue
        role = p.get("role")
        party_names = split_multi_person(name)
        for pname in party_names:
            is_split = len(party_names) > 1
            is_org = (not is_split) and _looks_like_org(pname)
            ent = ctx.org(pname, extra={"role": role}) if is_org \
                else ctx.person(pname, extra={"role": role})
            ctx.link(ent, REL_PARTY_OF, doc_node, metadata={"role": role})

    # Primary + labeled dates.
    if fields.get("primary_date"):
        ctx.link(doc_node, REL_DATED, ctx.date(fields["primary_date"], role="primary"))
    for d in (fields.get("dates") or []):
        if not isinstance(d, dict):
            continue
        val, lab = (d.get("value") or "").strip(), (d.get("label") or "").strip()
        if not val:
            continue
        ctx.link(doc_node, _universal_date_rel(lab), ctx.date(val, role=lab or None),
                 metadata={"label": lab} if lab else None)

    # Primary + labeled amounts → Money (has_total).
    if fields.get("primary_amount"):
        ctx.link(doc_node, REL_HAS_TOTAL, ctx.money(fields["primary_amount"]),
                 metadata={"label": "primary_amount"})
    for a in (fields.get("amounts") or []):
        if not isinstance(a, dict):
            continue
        val, lab = (a.get("value") or "").strip(), (a.get("label") or "").strip()
        if not val:
            continue
        ctx.link(doc_node, REL_HAS_TOTAL, ctx.money(val), metadata={"label": lab} if lab else None)

    # Identifiers → Identifier nodes (searchable), linked to the doc.
    for i in (fields.get("identifiers") or []):
        if not isinstance(i, dict):
            continue
        val, lab = (i.get("value") or "").strip(), (i.get("label") or "").strip()
        if not val:
            continue
        ctx.link(doc_node, "has_identifier", ctx.identifier(val, kind_tag=lab or "id"),
                 metadata={"label": lab} if lab else None)


# ── Generic handler — schema-agnostic graph mapping by field-key convention ──
# The DYNAMIC fallback: any schema (incl. a freshly-approved library type with no dedicated
# handler) is graphed automatically, so new schemas become graph-aware with zero new code.
_GEN_PERSON_ORG_KEYS = (
    "name", "vendor", "seller", "buyer", "customer", "issuer", "holder", "patient", "author",
    "payee", "payer", "merchant", "employer", "employee", "landlord", "tenant", "recipient",
    "applicant", "owner", "director", "shareholder", "insured", "physician", "supplier",
    "consignee", "shipper", "beneficiary", "subject", "party", "signatory", "organization",
    "organisation", "company", "manufacturer", "provider", "client", "borrower", "lender",
    "guarantor", "witness", "authority", "agent", "broker", "officer")
_GEN_DATE_KEYS = (
    "date", "expiry", "expiration", "issued", "dob", "birth", "due", "period", "valid",
    "effective", "start", "end", "deadline", "maturity")
_GEN_MONEY_KEYS = (
    "amount", "total", "price", "subtotal", "tax", "fee", "balance", "cost", "salary", "premium",
    "charge", "payment", "discount", "grand_total", "sum", "principal", "interest", "deposit",
    "rent", "revenue", "income", "expense")
_GEN_LOCATION_KEYS = (
    "jurisdiction", "country", "address", "city", "state", "province", "region",
    "location", "place_of_birth", "issuing_country")
_GEN_ID_KEYS = (
    "number", "_no", "_id", "reference", "sku", "account", "iban", "passport", "license",
    "registration", "serial", "policy", "claim", "tracking", "barcode", "doi", "isbn")
_GEN_SKIP = (
    "parties", "key_facts", "identifiers", "amounts", "dates", "records", "tags", "field_bboxes",
    "field_confidence", "field_mentions", "detected_doc_type", "detected_doc_subtype",
    "key_text_points", "title", "description", "summary", "confidence")


def _handle_generic(ctx: "_BootstrapCtx", fields: dict) -> None:
    """Schema-agnostic: map ANY extraction's fields into entities/relations by convention —
    people/orgs, dates, money, identifiers, and rows in nested arrays. Runs for any schema
    without a dedicated handler, so newly approved library schemas are graphed automatically."""
    doc = ctx.doc_node()

    def _has(key: str, words) -> bool:
        kl = key.lower()
        return any(w in kl for w in words)

    def _party(name, role):
        nm = (name or "").strip() if isinstance(name, str) else ""
        if not nm:
            return
        # Split joint-holder / co-applicant names so each person gets their
        # own Entity row and doc relation.  "GODA / KALYANI" → two parties.
        names = split_multi_person(nm)
        for person_name in names:
            # When split, each part is a person name — prefer person over
            # org even if ALL CAPS (bank statements print names in caps).
            is_split = len(names) > 1
            is_org = (not is_split) and _looks_like_org(person_name)
            ent = ctx.org(person_name, extra={"role": role}) if is_org else ctx.person(person_name, extra={"role": role})
            if ent:
                ctx.link(ent, REL_PARTY_OF, doc, metadata=({"role": role} if role else None))

    # 1. Standard universal-envelope arrays (present on most extractions).
    for p in (fields.get("parties") or []):
        if isinstance(p, dict):
            _party(p.get("name") or p.get("value"), p.get("role"))
    for it in (fields.get("identifiers") or []):
        if isinstance(it, dict) and it.get("value"):
            ident = ctx.identifier(str(it["value"]), kind_tag=str(it.get("label") or "id"))
            ctx.link(doc, "has_identifier", ident)
    for it in (fields.get("amounts") or []):
        if isinstance(it, dict) and it.get("value"):
            ctx.link(doc, REL_HAS_TOTAL, ctx.money(str(it["value"]), extra={"label": it.get("label")}))
    for it in (fields.get("dates") or []):
        if isinstance(it, dict) and it.get("value"):
            ctx.link(doc, REL_DATED, ctx.date(str(it["value"]), role=str(it.get("label") or "")))

    # 2. Custom schema fields, mapped by key convention.
    for k, v in fields.items():
        if k in _GEN_SKIP or v in (None, "", [], {}):
            continue
        if isinstance(v, dict):                       # object with a name (vendor{}, customer{})
            _party(v.get("name"), k)
        elif isinstance(v, str):                      # scalar string
            vs = v.strip()
            if not vs:
                continue
            if _has(k, _GEN_PERSON_ORG_KEYS):
                _party(vs, k)
            elif _has(k, _GEN_DATE_KEYS):
                ctx.link(doc, REL_DATED, ctx.date(vs, role=k))
            elif _has(k, _GEN_MONEY_KEYS):
                ctx.link(doc, REL_HAS_TOTAL, ctx.money(vs, extra={"role": k}))
            elif _has(k, _GEN_ID_KEYS):
                ident = ctx.identifier(vs, kind_tag=k)
                ctx.link(doc, "has_identifier", ident)
            elif _has(k, _GEN_LOCATION_KEYS):
                ctx.link(doc, REL_GOVERNED_BY, ctx.location(vs))
        elif isinstance(v, list) and v and isinstance(v[0], dict):   # array of rows
            people_key = _has(k, ("author", "party", "signator", "signatory", "director", "shareholder",
                                  "holder", "beneficiary", "witness", "member", "employee",
                                  "officer", "educat", "experien"))
            for it in v[:40]:
                if not isinstance(it, dict):
                    continue
                if people_key:
                    _party(it.get("name") or it.get("value"), k)
                amt = it.get("amount") or it.get("total") or it.get("value")
                if isinstance(amt, str) and amt.strip() and (_has(k, _GEN_MONEY_KEYS) or "amount" in it or "total" in it):
                    lbl = str(it.get("description") or it.get("name") or it.get("label") or "")[:60]
                    ctx.link(doc, REL_HAS_TOTAL, ctx.money(amt, extra={"line": lbl, "field": k}))

    # 3. key_facts → attributes on the doc hub (searchable metadata).
    kf = {str(it.get("label")): it.get("value") for it in (fields.get("key_facts") or [])
          if isinstance(it, dict) and it.get("label")}
    if kf:
        from sqlalchemy.orm.attributes import flag_modified
        meta = dict(doc.entity_metadata or {})
        meta.setdefault("key_facts", {})
        meta["key_facts"].update(kf)
        doc.entity_metadata = meta
        flag_modified(doc, "entity_metadata")


# ── Field reconciliation — schema-agnostic entity guarantee ──────────────
# Runs BEFORE the dedicated handler so every name-like value in extracted_fields
# gets an Entity row regardless of which handler processes the schema. Closes
# the extraction-recall gap (e.g. docs 36 & 43 where "Kalyani" is in fields but
# neither regex NER nor LLM NER created a person Entity row → graph undercount).
# Covers ALL entity kinds: person, org, location, date, money, identifier.


def _reconcile_fields(ctx: _BootstrapCtx, fields: dict) -> int:
    """Walk ALL leaf values in extracted_fields and guarantee an Entity row for
    every value that matches a known entity kind (person/org, location, date,
    money, identifier). Schema-agnostic — works for every doc type including
    those with dedicated handlers that may miss field variants.

    Idempotent: the entity-linking layer in _new_entity() deduplicates against
    existing non-deprecated entities (exact canonical, substring containment,
    Levenshtein ≤3 for person/org; same canonical for other kinds).

    Returns count of entities added.
    """
    added = 0

    def _has(key: str, words) -> bool:
        kl = key.lower()
        return any(w in kl for w in words)

    def _entity_exists(canonical: str) -> bool:
        """True if a non-deprecated person or org entity with this canonical
        already exists in the current doc — prevents reconciliation from
        creating a duplicate when the handler already covered the value."""
        from app.orm import Entity as _E
        return ctx.db.scalar(
            select(_E.pk).where(
                _E.document_pk == ctx.doc.pk,
                _E.kind.in_((KIND_PERSON, KIND_ORG)),
                _E.canonical == canonical,
                _E.deprecated_at.is_(None),
            ).limit(1)
        ) is not None

    def _handle_scalar(key: str, value: str) -> None:
        nonlocal added
        if not value or len(value) < 2:
            return
        vs = value.strip()
        if not vs:
            return
        # Classify by key name. Order matters: check more-specific kinds
        # (location, date, money, identifier) BEFORE the broad person/org
        # catch-all, so keys like issuer_address → location (via "address")
        # rather than person/org (via "issuer").
        if _has(key, _GEN_LOCATION_KEYS):
            if ctx.location(vs):
                added += 1
        elif _has(key, _GEN_DATE_KEYS):
            if ctx.date(vs, role=key):
                added += 1
        elif _has(key, _GEN_MONEY_KEYS):
            if ctx.money(vs, extra={"role": key}):
                added += 1
        elif _has(key, _GEN_ID_KEYS):
            if ctx.identifier(vs, kind_tag=key):
                added += 1
        elif _has(key, _GEN_PERSON_ORG_KEYS):
            # Split multi-person values (joint holders, co-applicants) so
            # each person gets their own Entity row.  E.g.
            # "GODA RAJESH / KALYANI GODA" → two person entities.
            names = split_multi_person(vs)
            for name in names:
                # When split_multi_person returns multiple parts, each part
                # is a person name (2+ words, passed _valid_name) — prefer
                # person over org even if ALL CAPS.  When the value wasn't
                # split (single-element list), use the normal heuristic.
                is_split = len(names) > 1
                is_org = (not is_split) and _looks_like_org(name)
                # Token-sorted canonical for person names so the existence
                # check matches entities created by ctx.person() (which also
                # uses canon_name_sorted).
                c = canon_org(name) if is_org else canon_name_sorted(name)
                if not c or _entity_exists(c):
                    continue
                ent = ctx.org(name, extra={"role": key}) if is_org else ctx.person(name, extra={"role": key})
                if ent:
                    added += 1

    def _walk(obj, key_path: str = "") -> None:
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k in _GEN_SKIP:
                    continue
                if isinstance(v, str):
                    _handle_scalar(k, v)
                elif isinstance(v, dict):
                    # Dict with a "name" field → treat as person/org entity
                    nm = v.get("name") or v.get("value")
                    if nm and isinstance(nm, str) and nm.strip():
                        _handle_scalar(k, nm)
                    else:
                        _walk(v, k)
                elif isinstance(v, list):
                    _walk(v, k)
                # Skip non-string scalars (int, float, bool, None)
        elif isinstance(obj, list):
            for item in obj[:40]:  # cap arrays at 40 entries
                if isinstance(item, dict):
                    _walk(item, key_path)
                elif isinstance(item, str) and item.strip():
                    _handle_scalar(key_path, item)

    _walk(fields)
    return added


_HANDLERS = {
    # fact_extractor (app/agents/fact_extractor.py) schemas:
    "universal": _handle_universal,
    "agreement": _handle_agreement,
    "receipt": _handle_receipt,
    "invoice": _handle_invoice,
    "bank_statement": _handle_bank_statement,
    "id_document": _handle_id_document,
    # Classifier labels that also produce id_document extraction — when the
    # documents product stores the raw classifier label (not the mapped
    # schema key) in extracted_fields.doc_type:
    "passport": _handle_id_document,
    "national_id": _handle_id_document,
    "driver_licence": _handle_id_document,
    "certificate": _handle_certificate,
    "policy_or_procedure": _handle_policy,
    # Income / revenue side (L4 mirror of the expense schemas)
    "revenue_invoice": _handle_revenue_invoice,
    "customer_payment": _handle_customer_payment,
    # Schemas without a prior dedicated handler (added 2026-07-26):
    "insurance_certificate": _handle_insurance_certificate,
    "business_profile": _handle_business_profile,
    "resume": _handle_resume,
    # kyc_extractor (app/agents/kyc_extractor.py) schemas — same semantics
    # as id_document but different field names because the KYC pipeline
    # predates the unified fact_extractor:
    "primary_photo_id": _handle_kyc_id,
    "passport_us": _handle_kyc_id,
    "passport_uk": _handle_kyc_id,
    "id_eu": _handle_kyc_id,
    "aadhaar": _handle_kyc_id,
    "pan": _handle_kyc_id,
    "nric": _handle_kyc_id,
    "id_au": _handle_kyc_id,
    "id_ca": _handle_kyc_id,
    "id_cn": _handle_kyc_id,
    "id_br": _handle_kyc_id,
    "id_jp": _handle_kyc_id,
    # bank_statement also defined in kyc_extractor (address proof case) —
    # same handler works since field shape overlaps.
    "address_proof": _handle_bank_statement,
}
