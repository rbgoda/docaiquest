"""LLM utilization rollup — pre-aggregate llm_call_audit into a small, bounded table.

- `period='day'` rows: the last 30 days, rebuilt each run (delete + reinsert prunes >30d).
- `period='month'` rows: one per (month, model), UPSERTed for recent months and PERSISTED
  after the raw ledger is purged — so monthly history survives retention.
- Both tenant-wide (`user_email=''`) and per-user, so the panel can show either.

Powers the admin 'Model utilization' panel (reads this instead of scanning llm_call_audit) and
lets the ledger purge be enabled without losing history. Cross-tenant (an ops/rollup job).
"""
from __future__ import annotations

import datetime as _dt
import logging

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db import SessionLocal
from app.orm import LLMCallAudit, LlmUsageRollup

log = logging.getLogger("docaiq.llm_rollup")
_CHUNK = 5000


def _agg(session, trunc: str, since: _dt.datetime, per_user: bool):
    b = func.date_trunc(trunc, LLMCallAudit.created_at)
    if per_user:
        ue = func.coalesce(LLMCallAudit.user_email, "(system)")
        sel = [LLMCallAudit.tenant_id, ue.label("ue"), b.label("bkt"),
               LLMCallAudit.provider, LLMCallAudit.model]
        grp = [LLMCallAudit.tenant_id, ue, b, LLMCallAudit.provider, LLMCallAudit.model]
    else:
        sel = [LLMCallAudit.tenant_id, b.label("bkt"), LLMCallAudit.provider, LLMCallAudit.model]
        grp = [LLMCallAudit.tenant_id, b, LLMCallAudit.provider, LLMCallAudit.model]
    q = (select(*sel,
                func.count().label("calls"),
                func.coalesce(func.sum(LLMCallAudit.input_tokens), 0).label("in_tok"),
                func.coalesce(func.sum(LLMCallAudit.output_tokens), 0).label("out_tok"))
         .where(LLMCallAudit.created_at > since).group_by(*grp))
    return session.execute(q).all()


def _rows(records, period: str, per_user: bool) -> list[dict]:
    out = []
    for r in records:
        out.append({
            "tenant_id": r.tenant_id,
            "user_email": (r.ue if per_user else ""),
            "period": period,
            "period_start": r.bkt,
            "provider": r.provider,
            "model": r.model,
            "calls": r.calls,
            "input_tokens": int(r.in_tok),
            "output_tokens": int(r.out_tok),
        })
    return out


def _chunks(seq: list, n: int):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def refresh(session) -> dict:
    """Rebuild day rows + upsert recent month rows (tenant-wide + per-user)."""
    now = _dt.datetime.now(_dt.timezone.utc)
    day_since = now - _dt.timedelta(days=30)
    month_since = now - _dt.timedelta(days=62)   # recompute current + previous month; older persist

    # 1. DAY — rebuild the last 30 days (delete + reinsert = idempotent + prunes >30d).
    session.execute(delete(LlmUsageRollup).where(LlmUsageRollup.period == "day"))
    day_rows = _rows(_agg(session, "day", day_since, False), "day", False) \
        + _rows(_agg(session, "day", day_since, True), "day", True)
    for chunk in _chunks(day_rows, _CHUNK):
        session.execute(pg_insert(LlmUsageRollup), chunk)

    # 2. MONTH — upsert recent months (older months' rows persist untouched).
    month_rows = _rows(_agg(session, "month", month_since, False), "month", False) \
        + _rows(_agg(session, "month", month_since, True), "month", True)
    for chunk in _chunks(month_rows, _CHUNK):
        stmt = pg_insert(LlmUsageRollup).values(chunk)
        stmt = stmt.on_conflict_do_update(
            constraint="uq_llm_rollup",
            set_={"calls": stmt.excluded.calls,
                  "input_tokens": stmt.excluded.input_tokens,
                  "output_tokens": stmt.excluded.output_tokens,
                  "updated_at": now})
        session.execute(stmt)

    session.commit()
    return {"day_rows": len(day_rows), "month_rows": len(month_rows)}


async def llm_rollup_task(ctx: dict) -> dict:
    with SessionLocal() as session:
        try:
            return {"status": "ok", **refresh(session)}
        except Exception as e:  # noqa: BLE001 — never crash the worker on a rollup
            session.rollback()
            log.warning("llm rollup failed: %s", e)
            return {"status": "error", "error": str(e)}
