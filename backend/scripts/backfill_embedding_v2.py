"""Backfill document_chunks.embedding_v2 with BGE-M3 vectors (Retrieval Step 2 · Phase 2a).

Idempotent: embeds only chunks whose embedding_v2 IS NULL (pass --all to re-embed everything).
Cross-tenant (admin backfill): a raw select(DocumentChunk) is not tenant-scoped. Batched; commits
per batch so it's resumable. The first run downloads BGE-M3 (~2.3GB) once.

Run inside the backend container:
    docker exec -e PYTHONPATH=/app -w /app <backend> python scripts/backfill_embedding_v2.py
"""
from __future__ import annotations

import sys
import time

from sqlalchemy import func, select

from app.db import SessionLocal
from app.embeddings import embed_v2
from app.orm import DocumentChunk


def main(force: bool = False, batch: int = 32) -> None:
    db = SessionLocal()
    total_all = db.scalar(select(func.count(DocumentChunk.pk)))
    q = select(DocumentChunk).order_by(DocumentChunk.pk)
    if not force:
        q = q.where(DocumentChunk.embedding_v2.is_(None))
    rows = db.scalars(q).all()
    total = len(rows)
    print(f"chunks total={total_all} · to backfill={total} (force={force})", flush=True)
    done = 0
    t0 = time.time()
    for i in range(0, total, batch):
        b = rows[i:i + batch]
        vecs = embed_v2([c.text for c in b])
        for c, v in zip(b, vecs):
            c.embedding_v2 = v
        db.commit()
        done += len(b)
        print(f"  {done}/{total}  ({round(time.time() - t0, 1)}s)", flush=True)
    print(f"DONE: backfilled {done} chunks in {round(time.time() - t0, 1)}s", flush=True)


if __name__ == "__main__":
    main(force="--all" in sys.argv)
