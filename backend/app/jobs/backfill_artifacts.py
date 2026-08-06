"""M44.P4.G · Backfill document artifacts for already-ingested docs.

Run once after migration 0041 lands. Walks every doc with
ingestion_status='ready' that doesn't yet have a document_artifacts
row and runs the materializer synchronously per doc.

Usage (inside the backend container):

    python -m app.jobs.backfill_artifacts            # backfill default tenant
    python -m app.jobs.backfill_artifacts --tenant acme

Idempotent · re-running on docs with artifacts already drops + re-creates.
For prod, set --skip-existing to only fill the gaps.
"""
from __future__ import annotations

import argparse
import logging
import sys

from sqlalchemy import select

from app.db import SessionLocal
from app.jobs.materialize_artifacts import materialize_for_document
from app.orm import Document, DocumentArtifact

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s · %(message)s")
log = logging.getLogger("docaiq.backfill_artifacts")


def main(tenant: str | None = None, skip_existing: bool = False) -> int:
    db = SessionLocal()
    try:
        stmt = select(Document).where(Document.ingestion_status == "ready")
        if tenant:
            stmt = stmt.where(Document.tenant_id == tenant)
        docs = db.scalars(stmt.order_by(Document.pk)).all()
        log.info("backfill: %d ready docs to consider", len(docs))

        if skip_existing:
            have = {
                a.document_pk for a in db.scalars(
                    select(DocumentArtifact).where(
                        DocumentArtifact.document_pk.in_([d.pk for d in docs])
                    )
                ).all()
            }
            docs = [d for d in docs if d.pk not in have]
            log.info("backfill: %d remaining after skip_existing filter", len(docs))

        ok = 0
        fail = 0
        for doc in docs:
            log.info("→ materializing doc pk=%d · %s · tenant=%s",
                     doc.pk, doc.name, doc.tenant_id)
            try:
                result = materialize_for_document(db, doc.pk, doc.tenant_id)
                log.info("  ✓ strategy=%s · %s",
                         result.get("processingStrategy"), result)
                ok += 1
            except Exception as e:  # noqa: BLE001
                log.exception("  ✗ failed: %s", e)
                fail += 1
        log.info("backfill complete · ok=%d · failed=%d", ok, fail)
        return 0 if fail == 0 else 1
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill document_artifacts rows")
    parser.add_argument("--tenant", default=None, help="Tenant id (default: all)")
    parser.add_argument("--skip-existing", action="store_true",
                        help="Skip docs that already have an artifact row")
    args = parser.parse_args()
    sys.exit(main(tenant=args.tenant, skip_existing=args.skip_existing))
