"""M44.P13 PR2 · Knowledge promoter · nightly Arq cron job.

Contributes each tenant's LOCAL understanding (extraction_corrections +
agent_skill_memory, ``source='local'``) to the control-plane global pool as
anonymized skeletons. Iterates distinct tenants so it works in both the
dedicated per-tenant container (one tenant) and the shared free container
(many). The privacy barrier + consent gating live in
``app/services/knowledge_promoter.py``; this is just the scheduled driver.

NEVER raises — returns a stats dict observable in the Arq result store.
"""
from __future__ import annotations

import logging

from sqlalchemy import text as _sql_text

from app.db import SessionLocal, set_current_tenant
from app.services.knowledge_promoter import promote_to_global

log = logging.getLogger("docaiq.jobs.knowledge_promoter")


async def knowledge_promote_task(ctx: dict) -> dict:
    """Arq entry. Returns per-run contribution stats. NEVER raises."""
    from app.license import is_cloud
    if not is_cloud():
        return {"status": "skipped", "reason": "oss"}
    stats: dict = {"tenants": 0, "contributed": 0, "rejected": 0, "errors": []}
    db = SessionLocal()
    try:
        rows = db.execute(
            _sql_text(
                "SELECT DISTINCT tenant_id FROM ("
                "  SELECT tenant_id FROM extraction_corrections WHERE source='local' "
                "  UNION "
                "  SELECT tenant_id FROM agent_skill_memory WHERE source='local'"
                ") u"
            )
        ).all()
        for (tenant_id,) in rows:
            stats["tenants"] += 1
            set_current_tenant(tenant_id)
            try:
                r = promote_to_global(db, tenant_id)
                stats["contributed"] += r.get("contributed", 0)
                stats["rejected"] += r.get("rejected", 0)
            except Exception as e:  # noqa: BLE001
                log.exception("knowledge promote failed for tenant %s", tenant_id)
                stats["errors"].append(f"{tenant_id}: {type(e).__name__}")
    finally:
        set_current_tenant(None)
        db.close()
    log.info("knowledge_promote_task done: %s", stats)
    return stats
