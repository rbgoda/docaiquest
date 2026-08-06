"""R5 · query router + CRAG pure-logic tests (no LLM / no DB)."""
from __future__ import annotations

from types import SimpleNamespace

from app.agents import crag
from app.services import query_router as qr


def _hit(pk, score):
    return SimpleNamespace(chunk_pk=pk, score=score)


# ── routing ──────────────────────────────────────────────────────────────────

def test_greeting_is_no_retrieval():
    assert qr.classify_route("hi") == qr.NO_RETRIEVAL
    assert qr.classify_route("thanks!") == qr.NO_RETRIEVAL
    assert qr.classify_route("") == qr.NO_RETRIEVAL


def test_meta_is_no_retrieval():
    assert qr.classify_route("what can you do?") == qr.NO_RETRIEVAL


def test_comparative_is_multi_hop():
    for q in [
        "Compare the totals on invoice A and invoice B",
        "What is the difference between the two policies?",
        "List the expiry date across all certificates",
        "Show the amount due for each document",
    ]:
        assert qr.classify_route(q) == qr.MULTI_HOP, q


def test_plain_question_is_single_hop():
    assert qr.classify_route("What is the total amount due?") == qr.SINGLE_HOP
    assert qr.classify_route("Who is the vendor on the invoice?") == qr.SINGLE_HOP


# ── merge_dedup ──────────────────────────────────────────────────────────────

def test_merge_dedup_keeps_best_score_and_caps():
    a = [_hit(1, 0.2), _hit(2, 0.9)]
    b = [_hit(1, 0.7), _hit(3, 0.5)]   # chunk 1 repeats with a higher score
    out = crag.merge_dedup([a, b], top_k=2)
    assert [h.chunk_pk for h in out] == [2, 1]      # sorted by score desc, capped to 2
    by = {h.chunk_pk: h.score for h in out}
    assert by[1] == 0.7                              # kept the better duplicate


def test_merge_dedup_handles_empty():
    assert crag.merge_dedup([[], []], top_k=5) == []


# ── CRAG score grade ─────────────────────────────────────────────────────────

def test_sufficient_true_when_hits_present():
    assert crag._sufficient([_hit(1, 0.8)], min_hits=1, min_top_score=None) is True


def test_sufficient_false_when_no_hits():
    assert crag._sufficient([], min_hits=1, min_top_score=None) is False


def test_sufficient_respects_min_top_score():
    assert crag._sufficient([_hit(1, 0.1)], min_hits=1, min_top_score=0.5) is False
    assert crag._sufficient([_hit(1, 0.9)], min_hits=1, min_top_score=0.5) is True
