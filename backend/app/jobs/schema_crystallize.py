"""Move-1 PR3 · schema crystallize · nightly Arq cron job.

Distils each tenant's stable LearnedSchema clusters into GeneratedSchema rows
(recurring doc types → concrete typed schemas the extractor can promote). Iterates
distinct tenants so it works in both the dedicated per-tenant and shared containers.
OFF unless DOCAIQ_SCHEMA_CRYSTALLIZE_ENABLED — the whole Move-1 crystallization
feature ships dormant. NEVER raises; returns a stats dict in the Arq result store.
"""
from __future__ import annotations

import logging

from sqlalchemy import text as _sql_text

from app.config import get_settings
from app.db import SessionLocal, set_current_tenant
from app.services.schema_crystallizer import crystallize_tenant

log = logging.getLogger("docaiq.jobs.schema_crystallize")


async def schema_crystallize_task(ctx: dict) -> dict:
    """Arq entry. Returns per-run crystallization stats. NEVER raises."""
    if not get_settings().schema_crystallize_enabled:
        return {"enabled": False}
    stats = {"enabled": True, "tenants": 0, "crystallized": 0, "skipped": 0, "errors": []}
    db = SessionLocal()
    try:
        rows = db.execute(_sql_text(
            "SELECT DISTINCT tenant_id FROM learned_schemas"
        )).all()
        for (tenant_id,) in rows:
            stats["tenants"] += 1
            set_current_tenant(tenant_id)
            try:
                r = crystallize_tenant(db, tenant_id)
                stats["crystallized"] += r.get("crystallized", 0)
                stats["skipped"] += r.get("skipped", 0)
                db.commit()
            except Exception as e:  # noqa: BLE001
                db.rollback()
                log.exception("schema crystallize failed for tenant %s", tenant_id)
                stats["errors"].append(f"{tenant_id}: {type(e).__name__}")
    finally:
        set_current_tenant(None)
        db.close()
    log.info("schema_crystallize_task done: %s", stats)
    return stats
