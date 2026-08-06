"""LLM-call ledger retention purge.

The `llm_calls` (per-call cost/latency ledger) and `llm_call_audit` (hash-only
compliance log) tables grow unbounded (~1.5M rows/mo at 1000 users × 50 docs).
This daily job deletes rows older than `llm_calls_retention_days`. OFF by default
(days=0) so an existing ledger is never purged on deploy — set the env to enable.

Batched DELETEs (so a first run on a huge table doesn't lock it / blow the WAL).
Cross-tenant by design (it's an ops/retention job, not a per-tenant read).
"""
from __future__ import annotations

import datetime as _dt
import logging

from sqlalchemy import delete, select

from app.config import get_settings
from app.db import SessionLocal

log = logging.getLogger("docaiq.purge_llm_ledger")

_BATCH = 5000


def _purge_table(session, model, cutoff) -> int:
    """Delete rows with created_at < cutoff in batches. Returns total deleted."""
    total = 0
    while True:
        ids = session.scalars(
            select(model.pk).where(model.created_at < cutoff).limit(_BATCH)
        ).all()
        if not ids:
            break
        session.execute(delete(model).where(model.pk.in_(ids)))
        session.commit()
        total += len(ids)
        if len(ids) < _BATCH:
            break
    return total


async def purge_llm_ledger_task(ctx: dict) -> dict:
    s = get_settings()
    days = int(s.llm_calls_retention_days or 0)
    if days <= 0:
        return {"status": "skipped", "reason": "retention disabled (days=0)"}

    from app.orm import LLMCall, LLMCallAudit

    cutoff = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=days)
    stats = {"llm_calls": 0, "llm_call_audit": 0}
    with SessionLocal() as session:
        try:
            stats["llm_calls"] = _purge_table(session, LLMCall, cutoff)
        except Exception as e:  # noqa: BLE001 — never crash the worker on a purge
            log.warning("purge llm_calls failed: %s", e)
        try:
            stats["llm_call_audit"] = _purge_table(session, LLMCallAudit, cutoff)
        except Exception as e:  # noqa: BLE001
            log.warning("purge llm_call_audit failed: %s", e)
    if stats["llm_calls"] or stats["llm_call_audit"]:
        log.info("purge_llm_ledger done (cutoff=%s): %s", cutoff.date(), stats)
    return stats
