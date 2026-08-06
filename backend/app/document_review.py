"""Document review · why-needs-review reasons + AI auto-approve.

Two responsibilities:

1. `review_reasons(doc_dict, *, threshold)` — given a document's API-shape
   dict (the same one /api/documents returns), compute a list of human-
   readable reasons explaining why it can't be safely auto-approved.
   Returns an empty list when the doc passes every blocker check AND
   meets the confidence threshold — i.e. it's safe to auto-approve.

2. `try_auto_approve(db, doc, *, threshold, ...)` — when reasons is empty
   and a threshold is configured, flip the doc to `reviewed` /
   `reviewed_by='ai-auto'` and record the audit-trail row in
   `document_reviews`. Idempotent — won't re-approve an already-reviewed
   doc.

The reasons surface in the frontend's "Why this needs review" banner
(in DocumentChatPanel) so the reviewer knows exactly which fields to
fill / which findings to resolve before approving.

The auto-approve threshold lives in `routing_config.thresholds.documentAutoApprove`
(per-tenant). Default is None — feature OFF.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_current_tenant
from app.orm import Document, DocumentReview, EntityRelation, RoutingConfig

# Required fields per docType. Empty values block auto-approve regardless
# of AI confidence — missing critical data means the AI was wrong even
# if it thought it was right.
REQUIRED_FIELDS: dict[str, list[str]] = {
    "receipt":          ["vendor_name", "date", "total", "currency"],
    "expense_claim":    ["vendor_name", "date", "total", "currency"],
    "revenue_invoice":  ["customer", "issue_date", "total", "currency"],
    "sales_receipt":    ["customer", "issue_date", "total", "currency"],
    "customer_payment": ["payer_name", "payment_date", "amount", "currency"],
}

# Doc types that participate in auto-approve. Statements (bank/CC) have a
# different review model (per-row sign-off, not whole-doc) — out of scope.
AUTO_APPROVE_DOC_TYPES = set(REQUIRED_FIELDS.keys())

# Sentinels that mean "categorizer punted, not a real category".
UNRECOGNISED_CATEGORIES = {"", "Other", "Uncategorised", "Other Income", "unknown"}


def _get(d: dict | None, *path: str, default: Any = None) -> Any:
    """Safe nested-dict accessor. _get(doc, 'extractedFields', 'fields', 'total')."""
    cur = d
    for p in path:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(p)
    return cur if cur is not None else default


def _parse_amount(s: Any) -> float | None:
    """Strip currency symbols + commas, return float or None."""
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    try:
        return float(str(s).replace(",", "").replace("$", "").replace("€", "").replace("£", "").strip())
    except (ValueError, TypeError):
        return None


def review_reasons(doc: dict, *, threshold: float | None = None,
                   duplicate_doc_ids: set[str] | None = None) -> list[dict]:
    """Compute the list of reasons this doc can't be auto-approved.

    Each reason is a dict: `{code, severity, message, hint}`. Empty list
    means the doc is safe to auto-approve at the given threshold.

    `severity` ∈ {info, warn, block}:
      - block: hard blocker (missing required field, duplicate, ingestion failed)
      - warn:  soft blocker (low confidence, math doesn't tie)
      - info:  context (no threshold configured)

    Callers should treat ANY non-empty list as "human review required" — the
    severity is only for UI styling and learning-loop weighting.
    """
    reasons: list[dict] = []
    doc_type = doc.get("docType") or ""
    fields = _get(doc, "extractedFields", "fields", default={}) or {}
    conf = _get(doc, "extractedFields", "confidence")
    doctype_conf = doc.get("docTypeConfidence")

    # 0. Ingestion failed — never auto-approve a broken extract.
    if doc.get("ingestionStatus") == "failed":
        reasons.append({
            "code": "ingestion_failed",
            "severity": "block",
            "message": "Extraction failed on this document.",
            "hint": "Re-classify or re-upload. AI couldn't extract structured fields.",
        })
        # If ingestion failed, the other checks are noise — return early.
        return reasons

    # 1. Doc type eligibility.
    from app.config import get_settings as _gs
    if _gs().product == "documents":
        # Documents product: the classifier emits 100+ types (invoice, passport,
        # certificate, …) that aren't in the audit AUTO_APPROVE_DOC_TYPES vocab.
        # Treat ANY confidently-classified type as eligible; only unclassified /
        # 'other' stays in human review. The confidence gates (#2/#3) below still
        # decide; the audit-specific checks (#4 required fields, #5 category) no-op
        # for these types since they have no REQUIRED_FIELDS entry.
        if (not doc_type) or doc_type.strip().lower() in ("other", "unknown", "unclassified", ""):
            return [{
                "code": "doctype_unsupported",
                "severity": "info",
                "message": f"Doc type '{doc_type or '(none)'}' isn't eligible for auto-approve.",
                "hint": "Unclassified / 'other' documents require human review.",
            }]
    elif doc_type not in AUTO_APPROVE_DOC_TYPES:
        return [{
            "code": "doctype_unsupported",
            "severity": "info",
            "message": f"Doc type '{doc_type}' isn't eligible for auto-approve.",
            "hint": "Statements + non-financial docs always require human review.",
        }]

    # 2. Low extraction confidence vs. threshold (warn — could still be human-OK).
    if threshold is not None:
        if conf is None:
            reasons.append({
                "code": "confidence_missing",
                "severity": "warn",
                "message": "AI didn't report an extraction confidence.",
                "hint": "Re-extract this doc, or review the fields manually.",
            })
        elif conf < threshold:
            reasons.append({
                "code": "low_confidence",
                "severity": "warn",
                "message": f"AI extraction confidence {int(conf * 100)}% < threshold {int(threshold * 100)}%.",
                "hint": "Spot-check vendor / date / total against the rendered doc.",
            })

    # 3. Low doc-type classification confidence.
    if doctype_conf is not None and doctype_conf < 0.75:
        reasons.append({
            "code": "low_doctype_confidence",
            "severity": "warn",
            "message": f"Doc-type classifier only {int(doctype_conf * 100)}% confident this is a {doc_type}.",
            "hint": "Open Reclassify if the type guess is wrong.",
        })

    # 4. Missing required fields — hard blocker.
    required = REQUIRED_FIELDS.get(doc_type, [])
    missing = []
    for f in required:
        val = fields.get(f)
        # Handle nested-dict customer/vendor objects too
        if val is None or val == "":
            missing.append(f)
        elif isinstance(val, dict) and not (val.get("name") or "").strip():
            missing.append(f)
    if missing:
        reasons.append({
            "code": "missing_required_field",
            "severity": "block",
            "message": f"Missing required field{'s' if len(missing) > 1 else ''}: {', '.join(missing)}.",
            "hint": f"Click the pencil icon on the {missing[0]} field to fill it in.",
        })

    # 5. Unrecognised category — categorizer punted, needs a human nudge.
    cat = (fields.get("category") or fields.get("revenue_category") or "").strip()
    if doc_type in ("receipt", "expense_claim", "revenue_invoice", "sales_receipt"):
        if cat in UNRECOGNISED_CATEGORIES:
            reasons.append({
                "code": "unrecognised_category",
                "severity": "warn",
                "message": "Category not recognised — the categorizer fell back to a generic bucket.",
                "hint": "Set a real category (e.g. Meals, Software, Travel) so the next merchant from this vendor auto-classifies.",
            })

    # 6. Math consistency — sum of line items should match total (±0.01).
    items = fields.get("items") or fields.get("line_items")
    total = _parse_amount(fields.get("total") or fields.get("amount"))
    if isinstance(items, list) and items and total is not None and total > 0:
        line_sum = 0.0
        all_priced = True
        for it in items:
            if not isinstance(it, dict):
                continue
            amt = _parse_amount(it.get("amount") or it.get("price") or it.get("total"))
            if amt is None:
                all_priced = False
                break
            line_sum += amt
        # Receipts routinely have tax + tip on top of line items — a strict
        # check fires constantly. Only flag when the gap is large enough
        # that a missing line item is the likely cause: >35% over (probably
        # missed an entire item) OR sum exceeds total at all (over-extraction).
        if all_priced and items:
            gap = total - line_sum
            pct_gap = gap / total if total > 0 else 0
            if pct_gap > 0.35:
                reasons.append({
                    "code": "math_inconsistent",
                    "severity": "warn",
                    "message": f"Line-item sum ({line_sum:.2f}) is well below total ({total:.2f}) — OCR may have missed an item.",
                    "hint": "Spot-check items against the original doc. (Normal tip + tax usually leaves a <30% gap.)",
                })
            elif gap < -0.01:
                # line_sum > total → can't happen legitimately; OCR doubled an item.
                reasons.append({
                    "code": "math_inconsistent",
                    "severity": "warn",
                    "message": f"Line-item sum ({line_sum:.2f}) EXCEEDS total ({total:.2f}) — OCR may have double-counted.",
                    "hint": "An item was probably extracted twice. Review the items list.",
                })

    # 7. Flagged as duplicate by the graph.
    doc_id = doc.get("id")
    if duplicate_doc_ids and doc_id and doc_id in duplicate_doc_ids:
        reasons.append({
            "code": "flagged_duplicate",
            "severity": "block",
            "message": "Graph flagged this as a likely duplicate of another doc.",
            "hint": "Open the Reconciliation findings to resolve or dismiss.",
        })

    return reasons


# ──────────────────────────────────────────────────────────────────────
# Threshold lookup + auto-approve
# ──────────────────────────────────────────────────────────────────────


def get_document_threshold(db: Session) -> float | None:
    """Read the tenant's documentAutoApprove threshold from routing_config.
    Returns None when the feature is off (default)."""
    tid = get_current_tenant()
    row = db.scalar(select(RoutingConfig).where(RoutingConfig.tenant_id == tid))
    if row is None:
        return None
    cfg = row.config or {}
    val = ((cfg.get("thresholds") or {}).get("documentAutoApprove"))
    if val is None:
        return None
    try:
        f = float(val)
        # Sanity-clamp — admin UI restricts but defend against bad config.
        return f if 0.5 <= f <= 1.0 else None
    except (ValueError, TypeError):
        return None


def get_duplicate_doc_ids(db: Session) -> set[str]:
    """Return the set of doc id_externals that participate in any
    DUPLICATE_OF relation for the current tenant. Cached at the request
    level by the caller (each ingestion job calls this once)."""
    tid = get_current_tenant()
    # Pull both endpoints of DUPLICATE relations. The graph stores them
    # with relation='duplicate_of' between two document entities.
    rows = db.execute(
        select(EntityRelation.src_entity_pk, EntityRelation.dst_entity_pk)
        .where(
            EntityRelation.tenant_id == tid,
            EntityRelation.relation == "duplicate_of",
        )
    ).all()
    if not rows:
        return set()
    # The endpoints are entity_pks; we need to map back to docs. Easier:
    # join EntityRelation → Entity → Document.
    from app.orm import Entity
    entity_pks: set[int] = set()
    for a, b in rows:
        entity_pks.add(a)
        entity_pks.add(b)
    if not entity_pks:
        return set()
    doc_ids = db.execute(
        select(Document.id_external)
        .join(Entity, Entity.document_pk == Document.pk)
        .where(Entity.pk.in_(entity_pks), Document.tenant_id == tid)
    ).all()
    return {r[0] for r in doc_ids}


def try_auto_approve(db: Session, doc: Document, *,
                     threshold: float | None = None,
                     duplicate_doc_ids: set[str] | None = None,
                     model_label: str = "ai-auto") -> bool:
    """If `doc` passes every review_reasons check at the given threshold,
    flip it to `reviewed` with `reviewed_by='ai-auto'` and write an audit
    row. Returns True when the flip happened, False otherwise.

    Idempotent — won't re-approve an already-reviewed doc. Caller is
    responsible for the commit (we only flush — keeps the auto-approve
    inside the same transaction as ingestion / fact-extract).

    `threshold` defaults to the tenant's routing_config setting. Pass
    explicitly to short-circuit the lookup (e.g. when invoking from a
    bulk job that already read it once).
    """
    if doc.review_status not in (None, "pending"):
        return False  # already reviewed — don't clobber a human signoff.
    if threshold is None:
        threshold = get_document_threshold(db)
    if threshold is None:
        return False  # feature off.

    # Re-build the API-shape dict the way _to_dict does, so review_reasons
    # sees the same view the frontend will. Avoid importing the repo to
    # keep this module dep-light.
    doc_dict = {
        "id": doc.id_external,
        "docType": doc.doc_type,
        "docTypeConfidence": doc.doc_type_confidence,
        "extractedFields": doc.extracted_fields,
        "ingestionStatus": doc.ingestion_status,
    }
    reasons = review_reasons(doc_dict, threshold=threshold,
                             duplicate_doc_ids=duplicate_doc_ids or set())
    if reasons:
        return False  # something still blocks auto-approve.

    # Flip + audit trail.
    tid = get_current_tenant()
    now = datetime.now(timezone.utc)
    prior = doc.review_status or "pending"
    doc.review_status = "reviewed"
    doc.reviewed_by = model_label
    doc.reviewed_at = now
    doc.review_note = f"Auto-approved (confidence ≥ {int(threshold * 100)}%)"
    db.add(DocumentReview(
        tenant_id=tid,
        document_pk=doc.pk,
        prior_status=prior,
        new_status="reviewed",
        reviewed_by=model_label,
        reason=doc.review_note,
        reviewed_at=now,
    ))
    db.flush()
    return True
