"""Re-embed all document_chunks with the currently-configured embedding backend.

Run AFTER switching DOCAIQ_EMBED_BACKEND (e.g. hash → local), because vectors
from different models aren't comparable. In-place: recomputes each chunk's
embedding from its stored text; does NOT re-parse files.

Usage (inside the backend container):
    docker compose -p <project> exec -T backend python scripts/reembed_chunks.py

Scoped to the container's tenant. Idempotent — safe to re-run.
"""
from __future__ import annotations

from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal, set_current_tenant
from app.embeddings import embed
from app.orm import DocumentChunk

BATCH = 50


def main() -> None:
    tid = get_settings().tenant_id
    set_current_tenant(tid)
    db = SessionLocal()
    chunks = db.scalars(select(DocumentChunk).where(DocumentChunk.tenant_id == tid)).all()
    print(f"backend={get_settings().embed_backend} dim={get_settings().embed_dim} "
          f"tenant={tid} chunks={len(chunks)}")
    for i in range(0, len(chunks), BATCH):
        batch = chunks[i:i + BATCH]
        for c, vec in zip(batch, embed([c.text or " " for c in batch])):
            c.embedding = vec
        db.commit()
        print(f"  re-embedded {min(i + BATCH, len(chunks))}/{len(chunks)}")
    print("done")


if __name__ == "__main__":
    main()
