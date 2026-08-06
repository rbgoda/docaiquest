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


async def enqueue_rematch(document_pk: int, tenant_id: str) -> str:
    """M28.6 · re-fire the matcher for a doc whose state changed via HITL
    (field edit, recategorize, review-status flip). Skips re-ingestion +
    classification + extraction (those haven't changed); just re-ranks
    requirement attachments against the current `extracted_fields`."""
    pool = await get_pool()
    job = await pool.enqueue_job("match_document_task", document_pk, tenant_id)
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


async def enqueue_kyc_extract(document_pk: int, tenant_id: str, attached_req_ids: list[str]) -> str:
    """Vision-based KYC field extraction. Decoupled from the matcher job
    so a 60s vision call doesn't hold the matcher's DB connection idle —
    see TODO #13 / matcher._maybe_extract_kyc_fields docstring."""
    pool = await get_pool()
    job = await pool.enqueue_job(
        "extract_kyc_fields_task", document_pk, tenant_id, attached_req_ids,
    )
    return job.job_id if job else ""
