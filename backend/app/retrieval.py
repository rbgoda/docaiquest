"""Hybrid retrieval over document_chunks.

Two parallel ranked lists fused with **Reciprocal Rank Fusion (RRF)** — a
parameter-free method that combines arbitrarily-scored rankers by rank, not
by score. Better than naive weighted sums when one ranker (e.g. our hash
embedding cosine) has wildly different score scale from the other (BM25).

* **BM25** — Postgres `ts_rank` on the `tsv` generated column (English
  dictionary). Always useful, regardless of embedding backend.
* **Cosine** — pgvector `<=>` distance via HNSW index. Quality depends on
  the configured embedding backend (M7's `hash` default is near-noise; flip
  to `openai` for real semantic retrieval).

Both queries filter by `tenant_id` and optionally by `document_id_external`.
"""

from __future__ import annotations

import hashlib
import logging
import random
import time
from dataclasses import dataclass

from sqlalchemy import and_, or_, select, text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.feature_flags import is_enabled, get_int, get_float
from app.db import get_current_tenant, get_current_vendor_pk
from app.documents_scope import get_current_owner_user_pk
from app.embeddings import embed_v2
from app.orm import Document, DocumentChunk, Entity
from app.reranker import is_available as _reranker_available, rerank as _rerank

log = logging.getLogger("docaiq.retrieval")

# RRF constant. 60 is the conventional value from the original RRF paper.
_RRF_K = 60
_DEFAULT_K = 12
_CANDIDATE_POOL = 200  # how many hits to consider from each sub-ranker before fusing. Must be >= top_k to prevent high-chunk-count docs from crowding out low-chunk-count docs in the candidate pool (e.g. "rajesh" in 171 chunks across 18 docs with pool=50 → only 10 docs surfaced)


@dataclass
class Hit:
    chunk_pk: int
    document_pk: int
    document_id_external: str
    document_name: str
    page: int
    text: str
    score: float                # fused RRF score
    bm25_rank: int | None       # 1-indexed; None if not in top pool
    cosine_rank: int | None


def retrieve(
    db: Session,
    query: str,
    *,
    top_k: int = _DEFAULT_K,
    doc_id_external: str | None = None,
    doc_pks: list[int] | None = None,
    bm25_terms: str = "",
) -> list[Hit]:
    """Return up to `top_k` chunks ranked by hybrid score (with optional
    cross-encoder rerank when settings.reranker_enabled).

    Stage 1 · Hybrid · BM25 + cosine fused via RRF · returns a
    candidate pool of size `reranker_top_k_initial` (20) when re-rank is
    enabled, or `top_k` otherwise.

    Stage 2 (when enabled) · BGE-Reranker-v2-m3 cross-encoder re-scores
    each (query, chunk_text) pair and the result is the new ordering.
    Top `top_k` of the reranked list is returned.
    """
    tenant_id = get_current_tenant()
    q = query.strip()
    if not q:
        return []

    t0 = time.perf_counter()  # M47 · retrieval latency tracking

    # Resolve the optional doc filter to a list of numeric pks so the SQL
    # below can stay cheap (no join needed in the hot path). Two callers:
    #   · single-doc chat passes `doc_id_external` → restrict to that one doc
    #   · cross-doc / workspace chat passes `doc_pks` → restrict to a set
    #     (e.g. one vendor's documents). An EMPTY list means "no docs in
    #     scope" → return [] rather than searching the whole tenant.
    restrict_pks: list[int] | None = None
    if doc_id_external:
        one = db.scalar(
            select(Document.pk).where(
                Document.tenant_id == tenant_id,
                Document.id_external == doc_id_external,
            )
        )
        if one is None:
            return []  # nonexistent doc → nothing to retrieve
        restrict_pks = [one]
    elif doc_pks is not None:
        if not doc_pks:
            return []  # scope resolved to zero docs → nothing to retrieve
        restrict_pks = list(doc_pks)

    # M17 · vendor-role isolation. A vendor-only user may ONLY retrieve over
    # their own documents — never the whole tenant. We intersect whatever the
    # caller asked for with the vendor's owned doc set (and default to it when
    # no filter was given), so a vendor can't read cross-vendor chunks even by
    # passing another vendor's doc_id or by omitting the filter entirely.
    vpk = get_current_vendor_pk()
    if vpk is not None:
        owned = set(db.scalars(
            select(Document.pk).where(
                Document.tenant_id == tenant_id, Document.vendor_pk == vpk
            )
        ).all())
        restrict_pks = [p for p in restrict_pks if p in owned] if restrict_pks else list(owned)
        if not restrict_pks:
            return []

    # M46 · Documents System per-user isolation. Identical reasoning to the
    # vendor block above, keyed on the logged-in user instead. Set ONLY in the
    # documents product, so in auditing this is a no-op. Guarantees a user can
    # never retrieve another user's chunks — even by passing a foreign doc_id
    # or omitting the scope to "ask across all".
    uid = get_current_owner_user_pk()
    if uid is not None:
        owned_by_user = set(db.scalars(
            select(Document.pk).where(
                Document.tenant_id == tenant_id, Document.owner_user_id == uid
            )
        ).all())
        restrict_pks = [p for p in restrict_pks if p in owned_by_user] if restrict_pks else list(owned_by_user)
        if not restrict_pks:
            return []

    # M43.P1 · stage 1 candidate pool. When the reranker is active we
    # inflate the pool so the cross-encoder has a wider working set; a
    # too-narrow pool prevents the reranker from rescuing genuinely
    # relevant chunks that landed at, say, RRF rank 12.
    settings = get_settings()
    use_reranker = is_enabled("reranker_enabled", False) and _reranker_available()
    initial_pool = max(top_k, get_int("reranker_top_k_initial", 20)) if use_reranker else top_k

    bm25_ranks = _bm25_ranking(db, q, tenant_id, restrict_pks, bm25_terms)
    cosine_ranks = _cosine_ranking(db, q, tenant_id, restrict_pks)

    fused = _rrf_fuse(bm25_ranks, cosine_ranks)
    candidate_pks = [pk for pk, _ in fused[:initial_pool]]
    # Doc-name rescue: identifier-like query tokens (WA07, EA07, 5534) live in the filename, not the
    # chunk text, so BM25 + cosine never surface them. Pull chunks from name/id-matching docs into
    # the pool so the reranker (and the agent) can work with the right document.
    name_pks, name_doc_pks = _docname_candidates(db, q, tenant_id, restrict_pks)
    if name_pks:
        candidate_pks = list(dict.fromkeys(candidate_pks + name_pks))
    # RAG-roadmap #3 · GraphRAG — union chunks whose EXTRACTED ENTITIES match a
    # distinctive query token so the reranker can surface them. Off by default →
    # no query runs → identical to today; on = a graph-anchored recall source.
    if is_enabled("graph_retrieval_enabled", True):
        graph_pks = _graph_candidates(db, q, tenant_id, restrict_pks)
        if graph_pks:
            candidate_pks = list(dict.fromkeys(candidate_pks + graph_pks))
    if not candidate_pks:
        return []

    # Fetch the chunk + parent document name in one shot. Re-apply the
    # `disabled` filter HERE as a single defense-in-depth choke point: every
    # candidate SOURCE filters disabled chunks EXCEPT the graph part-2 chunk-level
    # entity match, which returns Entity.chunk_pk directly. Without this, a chunk a
    # user disabled/redacted could resurface via a stale entity row when
    # graph_retrieval is on. Filtering on the final fetch covers all sources at once.
    rows = db.execute(
        select(
            DocumentChunk.pk, DocumentChunk.document_pk, DocumentChunk.page, DocumentChunk.text,
            DocumentChunk.chunk_index,
            Document.id_external, Document.name, Document.created_at,
        )
        .join(Document, Document.pk == DocumentChunk.document_pk)
        .where(DocumentChunk.pk.in_(candidate_pks), DocumentChunk.disabled.isnot(True))
    ).all()
    by_pk = {r.pk: r for r in rows}

    # ── Doc-level fairness ───────────────────────────────────────────────
    # When one document has many matching chunks, BM25 + cosine rankings can
    # fill the pool with that one doc's chunks, leaving other matching docs
    # unrepresented. Pull in at least ONE chunk per document whose raw text
    # contains the query terms so the reranker sees ALL matching documents.
    _q_lower = (q or "").lower().strip()
    if by_pk and _q_lower:
        _tokens = [t for t in _q_lower.split() if len(t) >= 2]
        if _tokens:
            _seen_doc_pks = {r.document_pk for r in by_pk.values()}
            _like_parts = []
            _like_params: dict[str, str] = {}
            for i, t in enumerate(_tokens):
                _like_parts.append(f"dc.text ILIKE :ft{i}")
                _like_params[f"ft{i}"] = f"%{t}%"
            _fair_sql = ("SELECT DISTINCT dc.document_pk FROM document_chunks dc "
                         "WHERE dc.tenant_id = :tenant AND dc.disabled IS NOT TRUE "
                         "AND (" + " OR ".join(_like_parts) + ")")
            _fair_params: dict = {"tenant": tenant_id, **_like_params}
            if restrict_pks:
                _fair_sql += " AND dc.document_pk = ANY(:doc_pks)"
                _fair_params["doc_pks"] = restrict_pks
            _all = {r[0] for r in db.execute(text(_fair_sql), _fair_params).all()}
            _missing = _all - _seen_doc_pks
            if _missing:
                _rescue = db.execute(text("""
                    SELECT DISTINCT ON (document_pk) pk FROM document_chunks
                    WHERE tenant_id = :t AND disabled IS NOT TRUE
                      AND document_pk = ANY(:m)
                    ORDER BY document_pk,
                             ts_rank(tsv, websearch_to_tsquery('english', :q))
                             + COALESCE(similarity(text, :q), 0) DESC
                """), {"t": tenant_id, "m": list(_missing), "q": q}).all()
                _new = [r[0] for r in _rescue]
                if _new:
                    candidate_pks = list(dict.fromkeys(candidate_pks + _new))
                    # Re-fetch the new chunks so by_pk includes them
                    _new_rows = db.execute(
                        select(DocumentChunk.pk, DocumentChunk.document_pk,
                               DocumentChunk.page, DocumentChunk.text,
                               DocumentChunk.chunk_index,
                               Document.id_external, Document.name, Document.created_at)
                        .join(Document, Document.pk == DocumentChunk.document_pk)
                        .where(DocumentChunk.pk.in_(_new),
                               DocumentChunk.disabled.isnot(True))
                    ).all()
                    for r in _new_rows:
                        by_pk[r.pk] = r
                    log.info("retrieval: fairness rescued %d docs (%d chunks) → %d total candidates",
                             len(_missing), len(_new), len(by_pk))

    # M43.P1 stage 2 · re-rank
    rrf_scores = {pk: score for pk, score in fused}
    if use_reranker:
        candidates_for_rerank: list[tuple[int, str]] = []
        for pk in candidate_pks:
            r = by_pk.get(pk)
            if r is not None:
                candidates_for_rerank.append((pk, r.text or ""))
        reranked = _rerank(q, candidates_for_rerank)
        ordered_pks = [pk for pk, _ in reranked[:top_k]]
        final_scores = {pk: score for pk, score in reranked}
        log.info("retrieval: reranker active · %d → %d", len(candidates_for_rerank), len(ordered_pks))
    else:
        ordered_pks = candidate_pks[:top_k]
        final_scores = rrf_scores

    # RAG-roadmap #5 · Normalize reranker logits to (0,1) via sigmoid BEFORE any
    # multiplicative boost below. Raw cross-encoder scores are often NEGATIVE, so
    # `score * factor` would INVERT (an older / upvoted doc with a negative logit
    # gets a *bigger*-magnitude negative score → demoted, the opposite of intent).
    # Sigmoid is monotonic, so the order is unchanged when NO boost runs — and we
    # only normalize when a boost is actually enabled, so the default path (both
    # boosts off, prod today) is byte-for-byte identical.
    if use_reranker and (is_enabled("retrieval_recency_enabled", False)
                         or is_enabled("retrieval_feedback_boost_enabled", False)):
        import math
        final_scores = {pk: 1.0 / (1.0 + math.exp(-min(30.0, max(-30.0, float(s)))))
                        for pk, s in final_scores.items()}

    # Recency weighting (opt-in) · multiply each candidate's score by a gentle
    # time-decay factor, then re-select top_k. Applied over the FULL candidate
    # pool so it can re-order across the rerank/RRF result, not just the slice.
    if is_enabled("retrieval_recency_enabled", False):
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        hl = max(1.0, get_float("retrieval_recency_half_life_days", 180.0))
        floor = min(1.0, max(0.0, get_float("retrieval_recency_floor", 0.5)))
        adjusted = {
            pk: float(final_scores.get(pk, 0.0))
                * _recency_factor(getattr(by_pk.get(pk), "created_at", None), now, hl, floor)
            for pk in candidate_pks
        }
        ordered_pks = sorted(candidate_pks, key=lambda p: adjusted.get(p, 0.0), reverse=True)[:top_k]
        final_scores = adjusted

    # #5 · per-user feedback boost (opt-in) · gently lift docs the CURRENT owner
    # marked answers "helpful" on, demote "unhelpful" ones. Bounded, so it nudges
    # ties without overriding relevance. Reads aggregated chat_feedback (no new
    # table); only runs when enabled, so off-path adds zero queries.
    if is_enabled("retrieval_feedback_boost_enabled", False):
        net = _feedback_net_by_doc(db, tenant_id)
        if net:
            strength = max(0.0, get_float("retrieval_feedback_boost_strength", 0.15))
            import math
            adj = {
                pk: float(final_scores.get(pk, 0.0))
                    * (1.0 + strength * math.tanh(net.get(getattr(by_pk.get(pk), "id_external", None), 0) / 3.0))
                for pk in candidate_pks
            }
            ordered_pks = sorted(candidate_pks, key=lambda p: adj.get(p, 0.0), reverse=True)[:top_k]
            final_scores = adj

    # Name-boost: when the query distinctively names specific doc(s), surface THOSE docs' best
    # chunks first — the identifier is usually in the filename (not the text), so the reranker
    # would otherwise bury the right document. Best chunk of each named doc first (by final score).
    if name_doc_pks:
        named_set = set(name_doc_pks)
        named = [pk for pk in candidate_pks
                 if getattr(by_pk.get(pk), "document_pk", None) in named_set]
        named.sort(key=lambda p: final_scores.get(p, 0.0), reverse=True)
        ns = set(named)
        ordered_pks = (named + [pk for pk in ordered_pks if pk not in ns])[:top_k]

    out: list[Hit] = []
    for pk in ordered_pks:
        r = by_pk.get(pk)
        if r is None:
            continue
        out.append(Hit(
            chunk_pk=pk,
            document_pk=r.document_pk,
            document_id_external=r.id_external,
            document_name=r.name,
            page=r.page,
            text=r.text,
            score=float(final_scores.get(pk, 0.0)),
            bm25_rank=_rank_of(pk, bm25_ranks),
            cosine_rank=_rank_of(pk, cosine_ranks),
        ))

    # Auto-merging: widen each hit's TEXT with its adjacent same-document chunks so
    # an answer straddling a chunk boundary is whole in the LLM context. Metadata
    # (chunk_pk/page/score) stays the matched child's → citations unchanged.
    if is_enabled("retrieval_context_expansion", False) and out:
        _expand_context(db, tenant_id, out, by_pk,
                        window=max(1, get_int("retrieval_context_window", 1)),
                        max_chars=max(400, get_int("retrieval_context_max_chars", 2400)))

    # M47 · retrieval quality observability — log per-query metrics for trending
    _log_retrieval_metrics(db, tenant_id, q, out, bm25_ranks, cosine_ranks, t0)

    return out


def _expand_context(db: Session, tenant_id: str, hits: list[Hit], by_pk: dict,
                    *, window: int, max_chars: int) -> None:
    """In-place: replace each hit's `.text` with itself + its `window` neighbouring
    chunks (same document, adjacent chunk_index), in reading order. One batched
    query for all neighbours; disabled chunks excluded; result capped at max_chars
    (centred on the matched child). No-op for a hit whose chunk_index is unknown."""
    targets = []  # (hit, document_pk, center_index)
    for h in hits:
        r = by_pk.get(h.chunk_pk)
        idx = getattr(r, "chunk_index", None) if r is not None else None
        if idx is not None:
            targets.append((h, h.document_pk, int(idx)))
    if not targets:
        return
    # Collect every (document_pk, chunk_index) we need across all hits.
    want: dict[int, set[int]] = {}
    for _h, dpk, idx in targets:
        want.setdefault(dpk, set()).update(range(idx - window, idx + window + 1))
    conds = [and_(DocumentChunk.document_pk == dpk, DocumentChunk.chunk_index.in_(sorted(idxs)))
             for dpk, idxs in want.items()]
    rows = db.execute(
        select(DocumentChunk.document_pk, DocumentChunk.chunk_index, DocumentChunk.text)
        .where(DocumentChunk.tenant_id == tenant_id, DocumentChunk.disabled.isnot(True), or_(*conds))
    ).all()
    text_at: dict[tuple[int, int], str] = {(r.document_pk, r.chunk_index): (r.text or "") for r in rows}
    for h, dpk, idx in targets:
        parts, seen = [], set()
        for j in range(idx - window, idx + window + 1):
            t = text_at.get((dpk, j))
            if t and t not in seen:
                parts.append(t)
                seen.add(t)
        merged = "\n\n".join(parts).strip()
        if not merged:
            continue
        if len(merged) > max_chars:  # keep the matched child centred within the budget
            center = h.text or ""
            room = max(0, max_chars - len(center)) // 2
            pos = merged.find(center)
            if pos >= 0:
                start = max(0, pos - room)
                merged = merged[start:start + max_chars]
            else:
                merged = merged[:max_chars]
        h.text = merged


def _feedback_net_by_doc(db: Session, tenant_id: str) -> dict[str, int]:
    """Per-document net feedback (#up − #down) for the CURRENT owner, from
    chat_feedback. Returns {doc_id_external: net}. Empty on any error / no owner —
    feedback weighting then no-ops. Owner-scoped (never cross-user)."""
    try:
        from app.documents_scope import get_current_owner_user_pk
        owner = get_current_owner_user_pk()
        if owner is None:
            return {}
        rows = db.execute(text(
            "select doc_id, "
            "sum(case when direction='up' then 1 when direction='down' then -1 else 0 end) net "
            "from chat_feedback where tenant_id=:t and owner_user_id=:o and doc_id is not null "
            "group by doc_id"), {"t": tenant_id, "o": owner}).all()
        return {r.doc_id: int(r.net or 0) for r in rows if r.doc_id}
    except Exception as e:  # noqa: BLE001 — never let feedback weighting break retrieval
        log.debug("feedback boost skipped: %s", e)
        return {}


def _recency_factor(created_at, now, half_life_days: float, floor: float) -> float:
    """Gentle exponential time-decay multiplier in [floor, 1.0]. A doc dated now
    → 1.0; one half-life old → floor + (1-floor)/2; very old → floor (never 0, so
    an old-but-highly-relevant doc is never buried). Unknown date → 1.0 (neutral)."""
    if created_at is None:
        return 1.0
    try:
        ts = created_at
        if ts.tzinfo is None:
            from datetime import timezone as _tz
            ts = ts.replace(tzinfo=_tz.utc)
        age_days = max(0.0, (now - ts).total_seconds() / 86400.0)
        decay = 0.5 ** (age_days / half_life_days)
        return floor + (1.0 - floor) * decay
    except Exception:  # noqa: BLE001 — never let recency math break retrieval
        return 1.0


# ---- Sub-rankers -----------------------------------------------------------
_NAME_STOP = {"document", "summary", "report", "statement", "details", "value", "number", "page",
              "information", "what", "show", "file", "the", "and", "this", "that", "with"}


def _docname_candidates(
    db: Session, query: str, tenant_id: str, restrict_pks: list[int] | None
) -> tuple[list[int], list[int]]:
    """Returns (chunk_pks, doc_pks) for documents whose NAME/id matches a DISTINCTIVE query token —
    digit-bearing (WA07, 20250839, INV-000065), an UPPERCASE acronym (ACLP, NRIC), or a long rare
    word (BookMyShow, Anoushka, Proforma). Narrow + capped at 4 docs so ordinary words don't drag in
    every doc. The identifier usually lives in the FILENAME, not the chunk text, so BM25+cosine miss
    it AND the reranker would bury it — hence pool-inclusion (chunk_pks) + a rank boost (doc_pks)."""
    import re as _re
    toks = [t for t in _re.findall(r"[A-Za-z0-9\-]{3,}", query or "")
            if (any(c.isdigit() for c in t) or (t.isupper() and len(t) >= 3) or len(t) >= 7)
            and t.lower() not in _NAME_STOP]
    if not toks:
        return [], []
    from sqlalchemy import or_ as _or
    conds = [Document.name.ilike(f"%{t}%") for t in toks] + \
            [Document.id_external.ilike(f"%{t}%") for t in toks]
    # Scope the name match to the CALLER's own docs (restrict_pks) — otherwise, in the
    # single-tenant documents product, OTHER users' identically-named files inflate the
    # count and trip the >4 "ambiguous" bail, suppressing the rescue for this user.
    doc_stmt = select(Document.pk).where(Document.tenant_id == tenant_id, _or(*conds))
    if restrict_pks:
        doc_stmt = doc_stmt.where(Document.pk.in_(restrict_pks))
    doc_pks = [r[0] for r in db.execute(doc_stmt).all()]
    if not doc_pks or len(doc_pks) > 4:   # ambiguous — matched too many docs; no rescue/boost
        return [], []
    stmt = (
        select(DocumentChunk.pk)
        .where(DocumentChunk.document_pk.in_(doc_pks), DocumentChunk.disabled.isnot(True))
        .order_by(DocumentChunk.document_pk, DocumentChunk.chunk_index)
        .limit(_CANDIDATE_POOL)
    )
    if restrict_pks:
        stmt = stmt.where(DocumentChunk.document_pk.in_(restrict_pks))
    return list(db.scalars(stmt)), doc_pks


def _graph_candidates(
    db: Session, query: str, tenant_id: str, restrict_pks: list[int] | None
) -> list[int]:
    """RAG-roadmap #3 · GraphRAG. Chunk pks reached via the entity graph. Two
    populations of `entities` exist and are handled differently:

      * L3 GRAPH nodes (source fact_bootstrap / llm_ner) — person / org / identifier
        with a normalized `canonical`, but DOC-level (`chunk_pk` NULL). These carry the
        real value: `canonical` catches surface-form variants BM25/cosine miss
        ('Mr. Goda Rajesh' ↔ 'goda rajesh balvantrai'). We match the query token to
        `canonical`/`text`, resolve to the DOC, and pull that doc's chunks.
      * REGEX nodes (money / date / email / id) — chunk-level (`chunk_pk` set). We match
        the surface text directly to the chunk.

    Owner-scoped (restrict_pks); capped at _CANDIDATE_POOL. Only when graph_retrieval_enabled."""
    import re as _re
    toks = [t for t in _re.findall(r"[A-Za-z0-9\-]{3,}", query or "")
            if (any(c.isdigit() for c in t) or (t.isupper() and len(t) >= 3) or len(t) >= 5)
            and t.lower() not in _NAME_STOP]
    if not toks:
        return []
    from sqlalchemy import or_ as _or
    canon_conds = [Entity.canonical.ilike(f"%{t.lower()}%") for t in toks]
    text_conds = [Entity.text.ilike(f"%{t}%") for t in toks]

    def _scope(stmt):
        return stmt.where(Entity.document_pk.in_(restrict_pks)) if restrict_pks else stmt

    # (1) doc-level canonical entities → the DOCS they belong to (bounded)
    graph_doc_pks = list(db.scalars(_scope(
        select(Entity.document_pk).distinct()
        .where(Entity.tenant_id == tenant_id, Entity.canonical.isnot(None),
               Entity.deprecated_at.is_(None), _or(*canon_conds))
    ).limit(8)).all())
    # (2) chunk-level (regex) entities → direct chunk match
    chunk_pks = [pk for pk in db.scalars(_scope(
        select(Entity.chunk_pk)
        .where(Entity.tenant_id == tenant_id, Entity.chunk_pk.isnot(None),
               Entity.deprecated_at.is_(None), _or(*text_conds))
    ).limit(_CANDIDATE_POOL)).all() if pk is not None]
    # pull the entity-matched docs' chunks into the pool (reranker then filters)
    if graph_doc_pks:
        cstmt = (select(DocumentChunk.pk)
                 .where(DocumentChunk.document_pk.in_(graph_doc_pks),
                        DocumentChunk.disabled.isnot(True))
                 .order_by(DocumentChunk.document_pk, DocumentChunk.chunk_index)
                 .limit(_CANDIDATE_POOL))
        if restrict_pks:
            cstmt = cstmt.where(DocumentChunk.document_pk.in_(restrict_pks))
        chunk_pks = list(dict.fromkeys(chunk_pks + list(db.scalars(cstmt).all())))
    return chunk_pks[:_CANDIDATE_POOL]


def _bm25_ranking(
    db: Session, query: str, tenant_id: str, doc_pk_in: list[int] | None,
    bm25_terms: str = "",
) -> list[tuple[int, float]]:
    """Return [(chunk_pk, raw_ts_rank), ...] in descending score order."""
    # Build optional doc filter into the SQL string — avoids ::int casts that
    # collide with SQLAlchemy's :param binding syntax.
    doc_clause = "AND document_pk = ANY(:doc_pks)" if doc_pk_in else ""
    # M47 · BM25 query enhancement: combine the raw query with LLM-generated
    # English search terms (bm25_terms) for non-English or keyword-rich queries.
    # e.g. "私のパスポート番号" + bm25_terms "passport number" → both searched.
    # websearch_to_tsquery handles the combined string naturally.
    search_q = (query or "").strip()
    if bm25_terms and bm25_terms.strip():
        search_q = f"{search_q} {bm25_terms.strip()}"
    # TODO #36 — recall fix. The original query had:
    #   AND tsv @@ plainto_tsquery('english', :q)
    # That hard filter rejected any chunk that didn't share a stemmed term
    # with the query, which meant short compliance queries ("MFA",
    # "ISO/IEC 27001:2022") stemmed to nothing and returned [].  Result:
    # validator said "no evidence" → looked like a model failure but was
    # really a retrieval bug.
    # Now: dual ranking — ts_rank (English keyword) + pg_trgm similarity
    # (language-agnostic trigrams). For English, ts_rank does the work.
    # For Hindi/Chinese/Arabic, similarity catches what ts_rank misses.
    # Both return 0 for non-matches; combined score = best of both worlds.
    # Uses pg_trgm extension (CREATE EXTENSION IF NOT EXISTS pg_trgm).
    sql = text(
        f"""
        SELECT pk,
               ts_rank(tsv, websearch_to_tsquery('english', :q))
               + COALESCE(similarity(text, :q), 0) AS s
          FROM document_chunks
         WHERE tenant_id = :tenant
           AND disabled IS NOT TRUE
           {doc_clause}
         ORDER BY s DESC, pk
         LIMIT :limit
        """
    )
    params: dict = {"q": search_q, "tenant": tenant_id, "limit": _CANDIDATE_POOL}
    if doc_pk_in:
        params["doc_pks"] = doc_pk_in
    return [(row.pk, float(row.s)) for row in db.execute(sql, params)]


def _cosine_ranking(
    db: Session, query: str, tenant_id: str, doc_pk_in: list[int] | None
) -> list[tuple[int, float]]:
    """Return [(chunk_pk, distance), ...] in ascending distance order
    (closer = better)."""
    # Retrieval Step 2 · BGE-M3 (1024d, multilingual) is the sole cosine retrieval column.
    # v1 MiniLM (384d) is still computed for backward compatibility but no longer queried.
    # The `col IS NOT NULL` guard means un-backfilled chunks are simply skipped.
    [qv] = embed_v2([query])
    col = "embedding_v2"
    doc_clause = "AND document_pk = ANY(:doc_pks)" if doc_pk_in else ""
    sql = text(
        f"""
        SELECT pk,
               {col} <=> :qv AS dist
          FROM document_chunks
         WHERE tenant_id = :tenant
           AND disabled IS NOT TRUE
           AND {col} IS NOT NULL
           {doc_clause}
         ORDER BY {col} <=> :qv
         LIMIT :limit
        """
    )
    params: dict = {
        "qv": _to_vector_literal(qv),
        "tenant": tenant_id,
        "limit": _CANDIDATE_POOL,
    }
    if doc_pk_in:
        params["doc_pks"] = doc_pk_in
    return [(row.pk, float(row.dist)) for row in db.execute(sql, params)]


def _to_vector_literal(vec: list[float]) -> str:
    """pgvector text input format. Faster than coercing through the ORM type
    decorator when called from raw SQL with `text()`."""
    return "[" + ",".join(f"{v:.6f}" for v in vec) + "]"


# ---- Fusion ----------------------------------------------------------------
def _rrf_fuse(
    bm25: list[tuple[int, float]],
    cosine: list[tuple[int, float]],
) -> list[tuple[int, float]]:
    """Reciprocal Rank Fusion. Combines two ranked lists by 1/(k+rank)."""
    scores: dict[int, float] = {}
    for rank, (pk, _) in enumerate(bm25, start=1):
        scores[pk] = scores.get(pk, 0.0) + 1.0 / (_RRF_K + rank)
    for rank, (pk, _) in enumerate(cosine, start=1):
        scores[pk] = scores.get(pk, 0.0) + 1.0 / (_RRF_K + rank)
    return sorted(scores.items(), key=lambda kv: kv[1], reverse=True)


def _rank_of(pk: int, ranking: list[tuple[int, float]]) -> int | None:
    for i, (other, _) in enumerate(ranking, start=1):
        if other == pk:
            return i
    return None



def _log_retrieval_metrics(
    db: Session,
    tenant_id: str,
    query: str,
    hits: list[Hit],
    bm25_ranks: list[tuple[int, float]],
    cosine_ranks: list[tuple[int, float]],
    t0: float,
) -> None:
    """Log retrieval quality metrics to the DB. Non-blocking — failures are silent.

    latency_ms measures end-to-end pipeline time from the pre-retrieval t0
    snapshot (includes expansion, reranking, and boost phases), not just ranking.
    """
    if not is_enabled("retrieval_metrics_enabled", True):
        return
    try:
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        top_score = round(hits[0].score, 4) if hits else 0.0
        # First 16 chars of MD5 (64 bits) — sufficient for query-trend
        # aggregation (~2³² entries before birthday collision), half the
        # index bloat of the full 32-char digest.
        qhash = hashlib.md5(
            query.lower().encode('utf-8'), usedforsecurity=False
        ).hexdigest()[:16]

        retention_days = get_int("retrieval_metrics_retention_days", 90)

        # Use a separate engine-level connection so the metrics INSERT commits
        # independently of the caller's transaction. Otherwise a downstream
        # rollback (chat pipeline, agent loop) would silently discard the row.
        # Reuse the same connection for the occasional retention purge.
        with db.bind.connect() as conn:
            conn.execute(
                text(
                    """INSERT INTO retrieval_metrics (tenant_id, qhash, hits_count, top_score,
                       bm25_candidates, cosine_candidates, latency_ms)
                       VALUES (:tid, :qh, :hc, :ts, :bc, :cc, :lt)"""
                ),
                {
                    "tid": tenant_id,
                    "qh": qhash,
                    "hc": len(hits),
                    "ts": top_score,
                    "bc": len(bm25_ranks),
                    "cc": len(cosine_ranks),
                    "lt": elapsed_ms,
                },
            )
            conn.commit()

            # Purge rows beyond the configured retention window. Runs inline
            # at low probability (~1:100) to amortise cost; batched at 5000
            # rows to avoid a single unbounded DELETE. Scoped to the current
            # tenant so one tenant's cleanup never touches another's data.
            if retention_days > 0 and random.random() < 0.01:
                conn.execute(
                    text(
                        "DELETE FROM retrieval_metrics "
                        "WHERE pk IN ("
                        "  SELECT pk FROM retrieval_metrics "
                        "  WHERE tenant_id = :tid "
                        "    AND created_at < NOW() - :retention * INTERVAL '1 day' "
                        "  LIMIT 5000"
                        ")"
                    ),
                    {"tid": tenant_id, "retention": retention_days},
                )
                conn.commit()
    except Exception:
        pass  # metrics must never break retrieval
