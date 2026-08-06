"""M44.P2.5 · DB-first cache layer over reflexion_pairs.

Single responsibility: given a new question, find a sufficiently-similar
prior question whose answer was marked HELPFUL by a reviewer, and return
that answer. Zero LLM calls. The caller (doc_chat.post_message) inserts
this lookup at the top of the chat path — before facts-first, before
retrieval, before the agent.

Why this is safe
----------------
* Cosine threshold (default 0.92) gates strictness. Below 0.92 is "kinda
  similar" and we don't want to serve a stale answer to a slightly
  different question.
* Reviewer thumbs-up gates quality. helpful_count >= 1 means at least
  one reviewer confirmed "yes this answer was good for this question".
* Tenant scoped via WHERE clause (defence in depth — the contextvar is
  already set by middleware, but never trust just one layer).
* Doc-scoped when doc_id_external is provided. A "what is the Aadhaar
  number?" hit on doc A doesn't serve as cache for the same question on
  doc B — they have different answers.

Why this is fast
----------------
pgvector HNSW index on `reflexion_pairs.question_embed` is already in
place (migration 0039 created it). A cosine search over the index is
O(log n) — single ms even with 100K rows.

Failure mode
------------
Returns None on any error (embed failure, DB hiccup, missing rows).
Caller falls through to the regular chat path. The cache is purely
additive — its job is to skip LLM calls when safe, never to block.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import text as _sql_text
from sqlalchemy.orm import Session

log = logging.getLogger("docaiq.reflexion_cache")


@dataclass
class CacheHit:
    """One match from the reflexion cache. The caller persists a new
    ChatMessage using `answer`, references `reflexion_pk` for telemetry."""
    reflexion_pk: int
    similarity: float
    question: str
    answer: str
    doc_id_external: str | None
    helpful_count: int
    iterations: int


def lookup(
    db: Session,
    *,
    tenant_id: str,
    question: str,
    doc_id_external: str | None = None,
    owner_user_id: int | None = None,
    similarity_threshold: float = 0.92,
    min_helpful: int = 1,
) -> CacheHit | None:
    """Return the best CacheHit for this question, or None.

    Filters applied:
      * tenant_id matches (defence in depth)
      * doc_id_external matches when provided (so an Aadhaar answer on
        doc-A doesn't masquerade as the answer on doc-B)
      * helpful_count >= min_helpful (curated by reviewer thumbs)
      * marked_unhelpful_count <= helpful_count (avoid net-negative)
      * cosine similarity >= threshold (semantic closeness)

    Embedding failures fail open (return None) so the caller falls
    through to the legacy path. NEVER raises.
    """
    if not question or not question.strip():
        return None

    try:
        from app.embeddings import embed as _embed_fn

        [q_vec] = _embed_fn([question])
        vec_lit = "[" + ",".join(f"{v:.6f}" for v in q_vec) + "]"

        # `<=>` is pgvector cosine DISTANCE (0=same, 2=opposite).
        # similarity = 1 - distance. We want distance <= 1 - threshold.
        max_distance = 1.0 - float(similarity_threshold)

        # Doc scoping clause assembled into the SQL to use the HNSW index
        # path either way.
        doc_filter = ""
        params: dict = {
            "qv": vec_lit,
            "tid": tenant_id,
            "max_d": max_distance,
            "min_h": int(min_helpful),
        }
        if doc_id_external:
            doc_filter = "AND doc_id_external = :did"
            params["did"] = doc_id_external
        # M46 · §4 · per-owner scope. When an owner is in context (documents
        # product) require an exact match — this excludes other users' rows AND
        # legacy NULL-owner rows, closing the cross-user leak. Auditing passes
        # owner_user_id=None → no filter → behaviour unchanged.
        owner_filter = ""
        if owner_user_id is not None:
            owner_filter = "AND owner_user_id = :owner"
            params["owner"] = int(owner_user_id)

        row = db.execute(
            _sql_text(f"""
                SELECT pk, question, final_answer, doc_id_external,
                       helpful_count, iterations,
                       question_embed <=> :qv AS dist
                  FROM reflexion_pairs
                 WHERE tenant_id = :tid
                   AND helpful_count >= :min_h
                   AND helpful_count >= marked_unhelpful_count
                   AND question_embed IS NOT NULL
                   AND (question_embed <=> :qv) <= :max_d
                   {doc_filter}
                   {owner_filter}
                 ORDER BY question_embed <=> :qv ASC
                 LIMIT 1
            """),
            params,
        ).first()
    except Exception as e:  # noqa: BLE001
        log.debug("cache lookup failed (non-fatal · fall through): %s", e)
        return None

    if row is None:
        return None

    similarity = max(0.0, min(1.0, 1.0 - float(row.dist)))
    log.info(
        "cache HIT · sim=%.3f · helpful=%d · reflex_pk=%d · doc=%s",
        similarity, row.helpful_count, row.pk, row.doc_id_external or "(any)",
    )
    return CacheHit(
        reflexion_pk=int(row.pk),
        similarity=similarity,
        question=row.question,
        answer=row.final_answer,
        doc_id_external=row.doc_id_external,
        helpful_count=int(row.helpful_count),
        iterations=int(row.iterations),
    )


def stats_last_7d(db: Session, tenant_id: str) -> dict:
    """Return chat-message breakdown by meta for the last 7 days.
    Used by /api/cache-stats to show savings in the UI."""
    rows = db.execute(
        _sql_text("""
            SELECT
              COALESCE(meta, 'single_shot') AS path,
              COUNT(*) AS n
            FROM chat_messages
            WHERE tenant_id = :tid
              AND role = 'ai'
              AND created_at > NOW() - INTERVAL '7 days'
            GROUP BY meta
            ORDER BY n DESC
        """),
        {"tid": tenant_id},
    ).all()
    total = sum(int(r.n) for r in rows)
    by_path = {str(r.path): int(r.n) for r in rows}
    # Zero-LLM buckets · meta values that resulted in zero LLM calls.
    # Note: existing `facts` path still calls LLM once to interpret the
    # JSON blob — it's not zero-LLM. `facts_det` (M44.P3.A) IS zero.
    # Old `facts` rows pre-P3 are counted here only when no LLM was made,
    # but since they all called the LLM we don't include them.
    zero_llm_count = (
        by_path.get("facts_det", 0)
        + by_path.get("cache_hit", 0)
        + by_path.get("identity_guard", 0)
    )
    # Also expose "cache_hit · ..." prefixed entries (the doc_chat router
    # writes meta="cache_hit · sim=0.97 · reflex=N", not just "cache_hit").
    for path_name, n in by_path.items():
        if path_name.startswith("cache_hit · ") and path_name != "cache_hit":
            zero_llm_count += n
    return {
        "total_ai_messages_7d": total,
        "by_path": by_path,
        "zero_llm_count": zero_llm_count,
        "zero_llm_pct": round((zero_llm_count / total * 100), 1) if total else 0.0,
    }
