"""R5 · Corrective-RAG (CRAG).

Grade retrieved evidence by retrieval score; when it's too weak, rewrite the
query once and re-retrieve, unioning the results — capped at `max_hops` so a
genuinely-unanswerable question still terminates (and then abstention handles
it upstream). Deterministic grading (no LLM); a single cheap LLM call only for
the query rewrite, and only when the first retrieval was weak.
"""
from __future__ import annotations

import logging

from app import abstention, retrieval

log = logging.getLogger("docaiq.crag")


def _sufficient(hits, *, min_hits: int, min_top_score: float | None) -> bool:
    """Score-based evidence grade — reuses the R1 abstention thresholds so CRAG
    and abstention agree on what 'enough evidence' means."""
    abstain, _why = abstention.assess_evidence(
        [getattr(h, "score", None) for h in hits],
        min_hits=min_hits, min_top_score=min_top_score,
    )
    return not abstain


def merge_dedup(hit_lists, top_k: int):
    """Union hits across queries, dedup by chunk_pk (keep the best score), and
    return the top_k by score. Preserves the Hit objects retrieval returns, so
    downstream evidence/citation code is unchanged."""
    best = {}
    for hits in hit_lists:
        for h in hits or []:
            cur = best.get(h.chunk_pk)
            if cur is None or (h.score or 0) > (cur.score or 0):
                best[h.chunk_pk] = h
    return sorted(best.values(), key=lambda h: (h.score or 0), reverse=True)[:top_k]


def refine_query(db, question: str) -> str | None:
    """One cheap LLM rewrite to recover from weak retrieval (spell out
    abbreviations, add synonyms). Returns None on failure → caller keeps the
    original query."""
    try:
        from app.services import doc_chat as _dc
        from app.llm.prompts import get_prompt
        out = _dc.llm_one_shot(
            db,
            get_prompt("crag_rewrite"),
            question, max_tokens=60,
        ).strip()
        # Guard against the model echoing a sentence/explanation.
        out = out.splitlines()[0].strip() if out else ""
        return out or None
    except Exception as e:  # noqa: BLE001
        log.warning("crag: query refine failed (%s); keeping original", e)
        return None


def corrective_retrieve(db, question, *, doc_pks, top_k, min_hits, min_top_score, max_hops=2):
    """Retrieve → grade → (if weak) rewrite + re-retrieve + union. Returns
    (hits, hops_used)."""
    hits = retrieval.retrieve(db, question, top_k=top_k, doc_pks=doc_pks)
    hops = 1
    while hops < max_hops and not _sufficient(hits, min_hits=min_hits, min_top_score=min_top_score):
        q2 = refine_query(db, question)
        if not q2 or q2.lower() == question.lower():
            break
        hits2 = retrieval.retrieve(db, q2, top_k=top_k, doc_pks=doc_pks)
        hits = merge_dedup([hits, hits2], top_k)
        hops += 1
        question = q2  # next correction (if any) builds on the rewrite
    return hits, hops
