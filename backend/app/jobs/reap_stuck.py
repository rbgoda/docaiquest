"""M49 · stuck-ingest reaper.

If a worker crashes (OOM, redis blip, enqueue-after-commit strand), a document
can be left in `pending`/`processing` forever — the frontend polls /status
indefinitely and chat hard-409s. This cron flips docs stuck past a generous
timeout to `failed` so the user can retry/re-upload. Self-healing: if a worker
IS still processing a falsely-reaped doc, its later `ready` commit wins.
"""
from __future__ import annotations

import datetime as _dt
import logging

from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal, set_current_tenant
from app.orm import Document

log = logging.getLogger("docaiq.reap")

# Worker job_timeout is 300s; anything still pending/processing after 30 min is
# stranded (well past even a slow vision-OCR ingest).
_STUCK_MINUTES = 30


async def reap_stuck_ingest_task(ctx) -> int:
    tid = get_settings().tenant_id
    set_current_tenant(tid)
    cutoff = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(minutes=_STUCK_MINUTES)
    n = 0
    try:
        with SessionLocal() as db:
            stuck = db.scalars(select(Document).where(
                Document.tenant_id == tid,
                Document.ingestion_status.in_(["pending", "processing"]),
                Document.created_at < cutoff)).all()
            for d in stuck:
                d.ingestion_status = "failed"
                d.ingestion_error = "ingestion timed out (auto-reaped)"
                n += 1
            if stuck:
                db.commit()
    except Exception as e:  # noqa: BLE001 — never let the reaper crash the worker
        log.warning("reaper: failed: %s", e)
        return 0
    if n:
        log.warning("reaper: marked %d stuck doc(s) failed", n)
    return n
