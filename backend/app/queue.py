"""Arq queue access from the API. Singleton pool so we don't pay handshake
cost on every upload."""

from __future__ import annotations

import asyncio

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from app.config import get_settings

_pool: ArqRedis | None = None
_pool_lock = asyncio.Lock()


def _redis_settings() -> RedisSettings:
    return RedisSettings.from_dsn(get_settings().redis_url)


async def get_pool() -> ArqRedis:
    global _pool
    if _pool is None:
        # Lock + double-check: concurrent first-uploads must not each create a pool
        # (the second would orphan the first and could fail in-flight enqueues).
        async with _pool_lock:
            if _pool is None:
                _pool = await create_pool(_redis_settings())
    return _pool


async def enqueue_ingest(document_pk: int, tenant_id: str) -> str:
    """Returns the job id (useful for debugging; we don't currently track it)."""
    pool = await get_pool()
    job = await pool.enqueue_job("ingest_document_task", document_pk, tenant_id)
    return job.job_id if job else ""


async def enqueue_reextract_type(type_slug: str, tenant_id: str) -> str:
    """Fired when a schema_library entry is APPROVED — re-extract every doc whose type resolves to
    `type_slug` so the newly-approved typed schema is applied without a manual backfill."""
    pool = await get_pool()
    job = await pool.enqueue_job("reextract_type_task", type_slug, tenant_id)
    return job.job_id if job else ""


async def enqueue_schema_autopilot(tenant_id: str, document_pk: int | None = None) -> str:
    """Adaptive Schema Loop — draft schemas for underserved docs. document_pk=None sweeps the whole
    corpus; a pk assesses+drafts just that doc (fired real-time after ingest)."""
    pool = await get_pool()
    job = await pool.enqueue_job("schema_autopilot_task", tenant_id, document_pk)
    return job.job_id if job else ""
