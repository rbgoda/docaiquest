"""T1.1 cleanup · re-embed every chunk in the tenant with the current
embed_backend.

Use after switching DOCAIQ_EMBED_BACKEND from hash → openai/gemini, since
old hash vectors aren't comparable to real-model vectors and cosine
retrieval will return noise until every chunk is re-embedded.

Idempotent · safe to re-run. Batches 32 chunks per API call.

Usage (inside backend container):

    DOCAIQ_TENANT_ID=test123 python3 scripts/reembed_all.py
    DOCAIQ_TENANT_ID=test123 python3 scripts/reembed_all.py --dry-run
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, "/app")

from sqlalchemy import select
from app.db import SessionLocal, current_tenant
from app.embeddings import embed
from app.config import get_settings
from app.orm import DocumentChunk


BATCH_SIZE = 32


def main() -> int:
    dry_run = "--dry-run" in sys.argv
    tenant = os.environ.get("DOCAIQ_TENANT_ID")
    if not tenant:
        print("Set DOCAIQ_TENANT_ID", file=sys.stderr)
        return 2

    s = get_settings()
    print(f"backend={s.embed_backend!r} dim={s.embed_dim} model={getattr(s, 'openai_embed_model', '?')}")
    if s.embed_backend == "hash":
        print("Refusing to run with hash backend — switch DOCAIQ_EMBED_BACKEND first.", file=sys.stderr)
        return 2

    current_tenant.set(tenant)
    with SessionLocal() as db:
        chunks = db.scalars(
            select(DocumentChunk).where(DocumentChunk.tenant_id == tenant)
        ).all()
        print(f"[{tenant}] {len(chunks)} chunks to re-embed")
        if not chunks:
            return 0

        if dry_run:
            print(f"[dry-run] would re-embed {len(chunks)} chunks in {(len(chunks) + BATCH_SIZE - 1) // BATCH_SIZE} batches")
            return 0

        done = 0
        for i in range(0, len(chunks), BATCH_SIZE):
            batch = chunks[i:i + BATCH_SIZE]
            texts = [c.text or " " for c in batch]
            try:
                vecs = embed(texts)
            except Exception as e:
                print(f"  batch {i//BATCH_SIZE} FAILED: {e}", file=sys.stderr)
                continue
            for c, v in zip(batch, vecs):
                c.embedding = v
            db.commit()
            done += len(batch)
            print(f"  re-embedded {done}/{len(chunks)}")
            time.sleep(0.1)  # gentle on API

    print(f"[{tenant}] done · {done} chunks re-embedded")
    return 0


if __name__ == "__main__":
    sys.exit(main())
