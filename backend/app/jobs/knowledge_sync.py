"""M44.P13 PR3 · Knowledge sync · Arq task (worker startup + nightly cron).

Seeds THIS container's tenant from the curated global pool (the receive side).
Runs once on worker startup so a freshly provisioned vanilla container boots
pre-loaded, and nightly so existing containers keep getting smarter.

NEVER raises — returns a stats dict observable in the Arq result store.
"""
from __future__ import annotations

import logging

from app.config import get_settings
from app.db import SessionLocal, set_current_tenant
from app.services.knowledge_seeder import sync_from_global

log = logging.getLogger("docaiq.jobs.knowledge_sync")


async def knowledge_sync_task(ctx: dict) -> dict:
    """Seed the container's own tenant from the global pool. NEVER raises."""
    from app.license import is_cloud
    if not is_cloud():
        return {"status": "skipped", "reason": "oss"}
    tid = get_settings().tenant_id
    db = SessionLocal()
    try:
        set_current_tenant(tid)
        return sync_from_global(db, tid)
    except Exception as e:  # noqa: BLE001
        log.exception("knowledge sync failed for tenant %s", tid)
        return {"status": "error", "reason": f"{type(e).__name__}", "seeded": 0}
    finally:
        set_current_tenant(None)
        db.close()
