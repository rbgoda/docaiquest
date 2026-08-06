"""M28.9 cleanup · re-evaluate existing requirement→document attachments
through the new structured precheck, detach false positives.

Walks every requirement that has `doc_id_external` set, runs
`app.agents.structured_match.check` against the (req, doc) pair, and:
  - If precheck PASSES → leaves attachment alone.
  - If precheck FAILS  → detaches (clears doc_id_external), bumps status
    to 'miss', records a `document_reviews` row with
    `new_status='match_rejected'` + the system as `reviewed_by='ai-cleanup'`
    so the audit trail captures who triggered the detachment.

Designed to be safe to re-run — idempotent. Detaching a doc that's
already not attached is a no-op.

Usage inside the backend container:

    DOCAIQ_TENANT_ID=test-audit-tech python3 scripts/cleanup_wrong_matches.py

Or for ALL tenants on this host (rare — usually you want per-tenant):

    DOCAIQ_TENANT_ID=test-audit-tech python3 scripts/cleanup_wrong_matches.py --all-tenants

Output: per-rejection log line + final summary by `constraint` kind
(country / period / doc_subtype) so you can spot patterns.
"""

from __future__ import annotations

import os
import sys
from collections import Counter
from typing import Any

from sqlalchemy import select

# Path setup so this works as a CLI inside the container's /app directory.
sys.path.insert(0, "/app")

from app.agents.structured_match import check as structured_check
from app.db import SessionLocal, current_tenant
from app.orm import Document, DocumentReview, Requirement


def _query_text(req: Requirement) -> str:
    """Build the same query string the matcher uses, so the precheck sees
    the exact text it'll see at match time."""
    parts = [req.title or "", req.subtitle or ""]
    if req.match_prompt:
        parts.append(req.match_prompt)
    if req.required_docs:
        parts.append(" ".join(str(s) for s in req.required_docs if s))
    return ". ".join(p for p in parts if p).strip()


def cleanup_tenant(db, tenant_id: str) -> dict[str, Any]:
    """Process every attached requirement for one tenant. Returns a stats dict."""
    current_tenant.set(tenant_id)

    attached_reqs = db.scalars(
        select(Requirement).where(
            Requirement.tenant_id == tenant_id,
            Requirement.doc_id_external.isnot(None),
        )
    ).all()

    stats: Counter = Counter()
    rejections: list[dict] = []
    print(f"\n=== Tenant {tenant_id} · {len(attached_reqs)} attached requirements ===")

    for req in attached_reqs:
        doc = db.scalar(
            select(Document).where(
                Document.tenant_id == tenant_id,
                Document.id_external == req.doc_id_external,
            )
        )
        if doc is None:
            # Dangling reference. Detach it; the doc is gone.
            print(
                f"  · {req.id_external} → DANGLING (doc {req.doc_id_external} missing)"
                f" · detaching"
            )
            req.prior_doc_id_external = req.doc_id_external
            req.doc_id_external = None
            req.confidence = None
            if req.status in ("ok", "warn"):
                req.status = "miss"
            stats["dangling"] += 1
            continue

        doc_fields = (doc.extracted_fields or {}).get("fields") if doc.extracted_fields else None
        verdict = structured_check(_query_text(req), doc_fields, doc.doc_type, req.group)
        if verdict.pass_:
            stats["kept"] += 1
            continue

        # FAIL → detach + record rejection.
        kind = verdict.constraint or "unknown"
        stats[f"rejected_{kind}"] += 1
        rejections.append({
            "req_id": req.id_external,
            "req_title": req.title,
            "doc_id": doc.id_external,
            "doc_name": doc.name,
            "doc_type": doc.doc_type,
            "constraint": kind,
            "reason": verdict.reason,
        })
        print(
            f"  ✗ {req.id_external}  ←  {doc.id_external} ({doc.doc_type})"
            f" · {kind}\n      {verdict.reason}"
        )

        # Detach.
        req.prior_doc_id_external = req.doc_id_external
        req.doc_id_external = None
        req.confidence = None
        if req.status in ("ok", "warn"):
            req.status = "miss"

        # Audit trail row — same shape as the M28.8 reject-match endpoint
        # so the learning loop can read both together.
        db.add(DocumentReview(
            tenant_id=tenant_id,
            document_pk=doc.pk,
            prior_status=doc.review_status or "pending",
            new_status="match_rejected",
            reviewed_by="ai-cleanup",
            reason=(verdict.reason or "")[:2000],
            metadata_json={
                "rejection_kind": "matcher_false_positive_cleanup",
                "constraint": kind,
                "rejected_doc_id": doc.id_external,
                "rejected_doc_name": doc.name,
                "rejected_doc_type": doc.doc_type,
                "requirement_id": req.id_external,
                "requirement_group": req.group,
                "requirement_title": req.title,
                "cleanup_source": "scripts/cleanup_wrong_matches.py",
            },
        ))

    db.commit()
    return {"stats": dict(stats), "rejections": rejections, "tenant": tenant_id}


def main() -> int:
    tenant = os.environ.get("DOCAIQ_TENANT_ID")
    if not tenant:
        print("Set DOCAIQ_TENANT_ID env var or pass via container env.", file=sys.stderr)
        return 2

    with SessionLocal() as db:
        result = cleanup_tenant(db, tenant)

    print("\n=== Summary ===")
    for k, v in sorted(result["stats"].items()):
        print(f"  {k}: {v}")
    if result["rejections"]:
        print(f"\nDetached {len(result['rejections'])} attachment(s). Requirements")
        print("are now in 'miss' status — they'll re-evaluate through the matcher")
        print("on the next ingestion-related event (HITL edit, re-upload, etc).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
