"""M47 · Scale-safe pipeline migration framework.

Patterns for migrating document pipelines without full re-ingestion:

1. DUAL-WRITE: Write both old + new vectors during migration. Serve from old
   while new backfills. Flip when complete. Used successfully for v1→v2 embed.

2. STALE-CHUNK DETECTION: `pipeline_version` column on document_chunks tracks
   which version produced each chunk. Query for stale chunks and re-process
   only those. Current version lives in config.pipeline_version.

3. LAZY BACKFILL: Process N stale chunks per worker tick (configurable rate).
   Runs as an Arq cron job so it doesn't block normal ingestion. Idempotent.

4. TARGETED REINGEST: `--since` / `--pipeline-version` filters on the reingest
   script limit scope to only affected documents.

Usage:
    from app.services.migration import stale_chunk_count, backfill_tick

    # How many chunks need updating?
    n = stale_chunk_count(db, tenant_id, min_version=2)

    # Process a batch (call from worker cron)
    done = backfill_tick(db, tenant_id, batch_size=20, min_version=2)
"""

from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.orm import Session

log = logging.getLogger("docaiq.migration")


def ensure_extensions(db: Session) -> None:
    """Idempotent: create pg_trgm extension + trigram index for multilingual BM25.
    Called at boot — safe to run on every deploy."""
    try:
        db.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        db.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_document_chunks_text_trgm "
            "ON document_chunks USING GIN (text gin_trgm_ops)"
        ))
        db.commit()
    except Exception as e:
        db.rollback()
        log.debug("migration: ensure_extensions: %s", e)


def ensure_pipeline_version_column(db: Session) -> None:
    """Idempotent: create pipeline_version column + index if they don't exist.
    Called at boot so new deploys auto-migrate without Alembic."""
    try:
        db.execute(text(
            "ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS "
            "pipeline_version INTEGER NOT NULL DEFAULT 1"
        ))
        db.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_document_chunks_pipeline_version "
            "ON document_chunks (pipeline_version)"
        ))
        db.commit()
    except Exception as e:
        db.rollback()
        log.debug("migration: ensure_pipeline_version_column: %s", e)


def stale_chunk_count(db: Session, tenant_id: str, *, min_version: int = 2) -> int:
    """How many chunks are below `min_version`? Returns 0 if column doesn't exist yet."""
    ensure_pipeline_version_column(db)
    try:
        return db.scalar(
            text(
                "SELECT COUNT(*) FROM document_chunks "
                "WHERE tenant_id = :tid AND pipeline_version < :ver"
            ),
            {"tid": tenant_id, "ver": min_version},
        ) or 0
    except Exception:
        return 0  # column doesn't exist yet (pre-migration)


def stale_document_pks(
    db: Session, tenant_id: str, *, min_version: int = 2, since: str | None = None,
) -> list[int]:
    """Return document PKs that have stale chunks (below min_version).
    Optional `since` ISO date filters to docs ingested before that date."""
    sql = (
        "SELECT DISTINCT document_pk FROM document_chunks "
        "WHERE tenant_id = :tid AND pipeline_version < :ver"
    )
    params: dict = {"tid": tenant_id, "ver": min_version}
    if since:
        sql += " AND document_pk IN (SELECT pk FROM documents WHERE tenant_id = :tid AND created_at < :since)"
        params["since"] = since
    try:
        rows = db.execute(text(sql), params).all()
        return [r[0] for r in rows]
    except Exception:
        return []


def backfill_tick(
    db: Session, tenant_id: str, *, batch_size: int = 20, min_version: int = 2,
) -> int:
    """Process one batch of stale chunks. Call from worker cron.
    Returns number of chunks processed (0 = nothing to do)."""
    pks = stale_document_pks(db, tenant_id, min_version=min_version)
    if not pks:
        return 0

    # Take a subset so one tick doesn't run forever
    batch = pks[:batch_size]
    from app.ingestion import ingest_document

    done = 0
    for doc_pk in batch:
        try:
            ingest_document(db, doc_pk, tenant_id)
            done += 1
        except Exception as e:
            log.warning("migration backfill: doc pk=%s failed: %s", doc_pk, e)
            db.rollback()
    log.info("migration backfill: processed %d/%d docs", done, len(batch))
    return done
