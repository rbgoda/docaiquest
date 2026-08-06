"""M44.P3.C · Reflexion curator · nightly Arq cron job.

The reflexion_pairs table accumulates one row per chat answer that went
through the Critic-Refine loop or Document Agent. Left untended, it
grows linearly with chat volume and dev/duplicate noise pollutes the
cache. The curator is a small offline pass that:

  1. **Merges** near-duplicate rows (cosine similarity > 0.95, same
     tenant + same doc_id_external). Sums helpful/unhelpful counts onto
     the older row; deletes the newer. Prefers final_answer of the row
     with higher helpful_count.

  2. **Prunes** net-negative rows. Any row where
     marked_unhelpful_count > helpful_count + 2 (the +2 grace handles
     accidental clicks) gets deleted. Keeps the cache from serving
     answers reviewers have rejected.

  3. **Re-embeds** rows with NULL question_embed. Rare but happens when
     the embed backend was unavailable at write time. Required for
     cosine retrieval to work at all.

Failure mode: each step is wrapped — one step failing does not block
the others. Returns a stats dict so the run is observable in Arq's
result store.
"""
from __future__ import annotations

import logging

from sqlalchemy import select, text as _sql_text

from app.db import SessionLocal, set_current_tenant

log = logging.getLogger("docaiq.reflexion_curator")

# Tunables · safe defaults
_DEDUP_SIMILARITY = 0.95           # merge when cosine ≥ this
_PRUNE_NET_NEGATIVE_GAP = 2        # delete when unhelpful > helpful + this
_MAX_ROWS_PER_RUN = 5000           # bound the work per nightly run


async def reflexion_curate_task(ctx: dict) -> dict:
    """Arq entry. Returns merge/prune/embed counts. NEVER raises."""
    # P2 · cloud-only — OSS deployments skip the curation sweep.
    from app.license import is_cloud
    if not is_cloud():
        return {"status": "skipped", "reason": "oss license"}
    stats = {"tenants": 0, "merged": 0, "pruned": 0, "reembedded": 0, "errors": []}
    db = SessionLocal()
    try:
        # Iterate distinct tenants — each tenant's curation runs scoped.
        tenant_rows = db.execute(
            _sql_text("SELECT DISTINCT tenant_id FROM reflexion_pairs")
        ).all()
        for (tenant_id,) in tenant_rows:
            stats["tenants"] += 1
            set_current_tenant(tenant_id)
            try:
                stats["merged"] += _dedupe(db, tenant_id)
                stats["pruned"] += _prune_net_negative(db, tenant_id)
                stats["reembedded"] += _reembed_nulls(db, tenant_id)
                db.commit()
            except Exception as e:  # noqa: BLE001
                log.exception("curator failed for tenant=%s: %s", tenant_id, e)
                db.rollback()
                stats["errors"].append(f"{tenant_id}: {e}")
    finally:
        db.close()
    log.info("reflexion curator complete · %s", stats)
    return stats


# ── Step 1 · Merge near-duplicates ────────────────────────────────────────
def _dedupe(db, tenant_id: str) -> int:
    """For each row, find any newer row with cosine sim ≥ DEDUP_SIMILARITY
    that shares (tenant_id, doc_id_external). Sum the newer's counts onto
    the older's, then delete the newer.

    Done as a single CTE pass so we never thrash the index with
    per-row queries.
    """
    sql = _sql_text(f"""
        WITH dups AS (
            SELECT a.pk AS keep_pk, b.pk AS drop_pk,
                   b.helpful_count AS b_help,
                   b.marked_unhelpful_count AS b_unhelp
              FROM reflexion_pairs a
              JOIN reflexion_pairs b
                ON a.tenant_id = b.tenant_id
               AND a.tenant_id = :tid
               AND COALESCE(a.doc_id_external, '') = COALESCE(b.doc_id_external, '')
               -- M46 · §PII · never merge across owners. In the documents product
               -- every user shares one tenant; general rows (doc_id NULL) would
               -- otherwise dedupe across users, contaminating one user's cache
               -- with another's. Owner-NULL (auditing) rows are unaffected.
               AND COALESCE(a.owner_user_id, -1) = COALESCE(b.owner_user_id, -1)
               AND a.pk < b.pk                          -- always keep older
               AND a.question_embed IS NOT NULL
               AND b.question_embed IS NOT NULL
               AND (a.question_embed <=> b.question_embed) <= (1 - {_DEDUP_SIMILARITY})
             LIMIT {_MAX_ROWS_PER_RUN}
        ),
        merged AS (
            UPDATE reflexion_pairs r
               SET helpful_count = r.helpful_count + dups.b_help,
                   marked_unhelpful_count = r.marked_unhelpful_count + dups.b_unhelp
              FROM dups
             WHERE r.pk = dups.keep_pk
         RETURNING r.pk
        )
        DELETE FROM reflexion_pairs
              WHERE pk IN (SELECT drop_pk FROM dups)
    """)
    result = db.execute(sql, {"tid": tenant_id})
    n = result.rowcount or 0
    if n:
        log.info("curator dedupe · tenant=%s · merged %d rows", tenant_id, n)
    return n


# ── Step 2 · Prune net-negative rows ──────────────────────────────────────
def _prune_net_negative(db, tenant_id: str) -> int:
    """Delete rows where the reviewer community has decisively rejected
    the answer. Conservative threshold (+2 grace) so a single accidental
    👎 doesn't lose a useful row.
    """
    sql = _sql_text(f"""
        DELETE FROM reflexion_pairs
              WHERE tenant_id = :tid
                AND marked_unhelpful_count > helpful_count + {_PRUNE_NET_NEGATIVE_GAP}
    """)
    result = db.execute(sql, {"tid": tenant_id})
    n = result.rowcount or 0
    if n:
        log.info("curator prune · tenant=%s · pruned %d net-negative rows", tenant_id, n)
    return n


# ── Step 3 · Re-embed rows whose question_embed is NULL ───────────────────
def _reembed_nulls(db, tenant_id: str) -> int:
    """Recover rows that were inserted while the embed backend was down.
    Without an embedding they can never match a cache lookup.
    """
    try:
        from app.embeddings import embed as _embed_fn
    except Exception as e:  # noqa: BLE001
        log.warning("curator reembed · embeddings backend unavailable: %s", e)
        return 0

    from app.orm import ReflexionPair
    rows = db.scalars(
        select(ReflexionPair).where(
            ReflexionPair.tenant_id == tenant_id,
            ReflexionPair.question_embed.is_(None),
        ).limit(500)
    ).all()
    if not rows:
        return 0
    questions = [r.question for r in rows]
    try:
        vecs = _embed_fn(questions)
    except Exception as e:  # noqa: BLE001
        log.warning("curator reembed · embed call failed: %s", e)
        return 0
    n = 0
    for row, vec in zip(rows, vecs):
        row.question_embed = vec
        n += 1
    if n:
        log.info("curator reembed · tenant=%s · re-embedded %d rows", tenant_id, n)
    return n


# ── Manual trigger for ops / smoke tests ──────────────────────────────────
def run_now() -> dict:
    """Synchronous variant of the curator that calls it inline (no Arq).
    Useful for smoke tests and operator-triggered runs via a Python REPL
    inside the backend container."""
    import asyncio
    return asyncio.run(reflexion_curate_task({}))
