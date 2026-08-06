"""R5 · Query router + multi-hop decomposition.

Routes a chat question to one of three strategies, then retrieves evidence with
corrective-RAG (CRAG):

  · no_retrieval — greeting / meta / chit-chat (no real document lookup needed)
  · single_hop  — a normal factual question → one CRAG retrieve
  · multi_hop   — comparative / cross-document / aggregation → decompose into
                  sub-questions, CRAG-retrieve each, and union the evidence so
                  the answer LLM can compose across documents.

Routing is heuristic (no LLM) for the common cases to keep latency/cost down;
decomposition + the CRAG rewrite are the only LLM calls, and only on the paths
that need them. Flag-gated by `chat_query_routing` (default off) — when off the
caller keeps its original single `retrieval.retrieve`.
"""
from __future__ import annotations

import logging
import re

from app.agents import crag

log = logging.getLogger("docaiq.query_router")

NO_RETRIEVAL = "no_retrieval"
SINGLE_HOP = "single_hop"
MULTI_HOP = "multi_hop"

_GREETING_RE = re.compile(r"^\s*(hi|hello|hey|thanks|thank you|ok|okay|yo|cool|great)\b[\s!.?]*$", re.I)
_META_RE = re.compile(r"\b(what can you do|who are you|how do you work|what are you|help me use)\b", re.I)
# Comparative / multi-entity / aggregation cues → multi-hop decomposition.
_MULTI_RE = re.compile(
    r"\b(compare|comparison|versus|vs\.?|difference(?:s)? between|differ|"
    r"both|each (?:of|document|doc|file)|across (?:all|the|every)|every document|"
    r"all (?:the )?(?:documents|invoices|docs|files|policies|certificates)|"
    r"as well as|and also)\b",
    re.I,
)


def classify_route(question: str, history=None) -> str:
    q = (question or "").strip()
    if not q:
        return NO_RETRIEVAL
    if _GREETING_RE.match(q) or _META_RE.search(q):
        return NO_RETRIEVAL
    if _MULTI_RE.search(q):
        return MULTI_HOP
    return SINGLE_HOP


def decompose(db, question: str, *, max_subqs: int = 4) -> list[str]:
    """Break a multi-hop question into the minimal independent sub-questions.
    Falls back to [question] on any failure (so the caller still answers)."""
    try:
        from app.services import doc_chat as _dc
        out = _dc.llm_one_shot(
            db,
            "Break the user's question into the minimal set of independent "
            f"sub-questions needed to answer it (at most {max_subqs}). Output one "
            "sub-question per line, no numbering or bullets. If the question is "
            "already atomic, output it unchanged.",
            question, max_tokens=160,
        )
        subs = []
        for ln in (out or "").splitlines():
            s = re.sub(r"^[\s\-\d.)•]+", "", ln).strip()
            if len(s) > 3:
                subs.append(s)
        return subs[:max_subqs] or [question]
    except Exception as e:  # noqa: BLE001
        log.warning("query_router: decompose failed (%s); using whole question", e)
        return [question]


def route_and_retrieve(db, question, *, doc_pks, top_k, min_hits, min_top_score,
                       max_hops=2, max_subqs=4, history=None):
    """Route → retrieve (with CRAG). Returns (hits, meta). `hits` are the same
    Hit objects `retrieval.retrieve` returns, so the answer/citation path is
    unchanged."""
    route = classify_route(question, history)
    meta: dict = {"route": route}

    if route == MULTI_HOP:
        subqs = decompose(db, question, max_subqs=max_subqs)
        meta["sub_questions"] = subqs
        hit_lists, hops = [], 0
        for sq in subqs:
            h, used = crag.corrective_retrieve(
                db, sq, doc_pks=doc_pks, top_k=top_k,
                min_hits=min_hits, min_top_score=min_top_score, max_hops=max_hops,
            )
            hit_lists.append(h)
            hops += used
        hits = crag.merge_dedup(hit_lists, top_k)
        meta["hops"] = hops
        return hits, meta

    # single_hop / no_retrieval → one CRAG retrieve (no decomposition).
    hits, hops = crag.corrective_retrieve(
        db, question, doc_pks=doc_pks, top_k=top_k,
        min_hits=min_hits, min_top_score=min_top_score, max_hops=max_hops,
    )
    meta["hops"] = hops
    return hits, meta
