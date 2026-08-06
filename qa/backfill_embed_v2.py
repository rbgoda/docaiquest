"""Backfill document_chunks.embedding_v2 via the configured v2 backend (DashScope text-embedding-v4,
1024d). Embeds each chunk's RAW text (matches ingestion + the A/B eval) and writes embedding_v2,
leaving the serving `embedding` (v1) column untouched — so this is safe to run before flipping
embed_v2_active, and the flip stays instantly reversible.

Run in the backend container (requires DOCAIQ_EMBED_V2_BACKEND=dashscope in env):
    docker exec -e PYTHONPATH=/app -w /app <backend> python /app/qa/backfill_embed_v2.py [--all]

Default: only chunks missing embedding_v2. --all: re-embed every chunk.
"""
import sys

from sqlalchemy import select, text as _sql
from app.db import SessionLocal
from app.config import get_settings
from app.embeddings import embed_v2
from app.contextual import embedding_input
from app.orm import DocumentChunk

BATCH = 30

if __name__ == "__main__":
    do_all = "--all" in sys.argv
    s = get_settings()
    print(f"embed_v2_backend={s.embed_v2_backend} · embed_v2_dim={s.embed_v2_dim} · "
          f"model={s.dashscope_embed_model}", flush=True)
    if s.embed_v2_backend != "dashscope":
        print("WARNING: embed_v2_backend is not 'dashscope' — set DOCAIQ_EMBED_V2_BACKEND=dashscope "
              "before backfilling, or this will use the local BGE-M3 model.", flush=True)
    db = SessionLocal()
    q = select(DocumentChunk).order_by(DocumentChunk.pk)
    if not do_all:
        q = q.where(DocumentChunk.embedding_v2.is_(None))
    rows = db.scalars(q).all()
    total = len(rows)
    print(f"chunks to embed: {total}", flush=True)
    done = 0
    for i in range(0, total, BATCH):
        batch = rows[i:i + BATCH]
        try:
            # Contextual-retrieval representation (context + text), matching v1 + ingestion.
            vecs = embed_v2([embedding_input(c.text, c.context_summary) for c in batch])
        except Exception as e:  # noqa: BLE001
            print(f"  batch {i} FAILED: {e}", flush=True)
            continue
        for c, v in zip(batch, vecs):
            c.embedding_v2 = v
        db.commit()
        done += len(batch)
        print(f"  {done}/{total}", flush=True)
    # verify coverage
    missing = db.scalar(select(DocumentChunk).where(DocumentChunk.embedding_v2.is_(None)).limit(1))
    covered = db.scalar(_sql("select count(*) from document_chunks where embedding_v2 is not null"))
    tot = db.scalar(_sql("select count(*) from document_chunks"))
    print(f"\nembedding_v2 coverage: {covered}/{tot} · any still missing: {missing is not None}", flush=True)
    print("DONE", flush=True)
    db.close()
