"""M44.P9.1 · Re-embed existing rows after an embedding-backend swap.

When you flip DOCAIQ_EMBED_BACKEND (e.g. from `hash` to `dashscope`),
existing vectors in document_chunks and reflexion_pairs were produced
by the OLD backend and aren't comparable to fresh ones from the new
backend. This script re-embeds them in place.

Tables affected:
  · document_chunks.embedding         (one vector per chunk)
  · reflexion_pairs.question_embed    (one vector per cached question)

Idempotent: re-running just overwrites with the same backend's vectors.
Safe to run while traffic is live; UPDATEs are atomic per row.

Usage (inside the backend container):
  python -m app.jobs.reembed --tenant default
"""
from __future__ import annotations

import argparse
import logging
import sys
import time

from sqlalchemy import select

from app.db import SessionLocal
from app.embeddings import embed as _embed_fn
from app.orm import DocumentChunk, ReflexionPair

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s · %(message)s")
log = logging.getLogger("docaiq.reembed")

_BATCH = 10  # Dashscope intl cap


def reembed_chunks(db, tenant_id: str) -> int:
    """Re-embed all document_chunks in the tenant. Batched to keep
    embedder calls small and DB locks short."""
    stmt = select(DocumentChunk).where(DocumentChunk.tenant_id == tenant_id).order_by(DocumentChunk.pk)
    rows = db.scalars(stmt).all()
    log.info("reembed: %d chunks for tenant=%s", len(rows), tenant_id)

    done = 0
    t0 = time.perf_counter()
    for i in range(0, len(rows), _BATCH):
        batch = rows[i:i + _BATCH]
        # Use context_summary + text if available (matches the indexer's
        # contextual-retrieval pattern from M43.P1.B)
        texts = [
            ((r.context_summary or "") + "\n\n" + (r.text or "")).strip()
            for r in batch
        ]
        try:
            vecs = _embed_fn(texts)
        except Exception as e:  # noqa: BLE001
            log.warning("reembed chunks batch %d: %s", i, e)
            continue
        for r, v in zip(batch, vecs):
            r.embedding = v
        db.flush()
        done += len(batch)
        if done % 100 == 0 or done == len(rows):
            el = int(time.perf_counter() - t0)
            log.info("  %d/%d chunks (%ds)", done, len(rows), el)
    db.commit()
    return done


def reembed_reflexion(db, tenant_id: str) -> int:
    """Re-embed all reflexion_pairs question_embed values."""
    stmt = select(ReflexionPair).where(ReflexionPair.tenant_id == tenant_id).order_by(ReflexionPair.pk)
    rows = db.scalars(stmt).all()
    log.info("reembed: %d reflexion rows for tenant=%s", len(rows), tenant_id)
    if not rows:
        return 0

    done = 0
    for i in range(0, len(rows), _BATCH):
        batch = rows[i:i + _BATCH]
        texts = [r.question or " " for r in batch]
        try:
            vecs = _embed_fn(texts)
        except Exception as e:  # noqa: BLE001
            log.warning("reembed reflexion batch %d: %s", i, e)
            continue
        for r, v in zip(batch, vecs):
            r.question_embed = v
        db.flush()
        done += len(batch)
    db.commit()
    return done


def main(tenant: str) -> int:
    db = SessionLocal()
    try:
        n_chunks = reembed_chunks(db, tenant)
        n_reflex = reembed_reflexion(db, tenant)
        log.info("reembed complete · chunks=%d · reflexion=%d", n_chunks, n_reflex)
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--tenant", default="default")
    args = p.parse_args()
    sys.exit(main(args.tenant))
