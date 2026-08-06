"""RAG-roadmap #3 · _graph_candidates token gate — no distinctive query token means
no DB query at all (proven by passing db=None). Off-flag path adds zero queries."""
from __future__ import annotations

from app.retrieval import _graph_candidates


def test_no_distinctive_tokens_returns_empty_without_touching_db():
    # db=None would raise if the function tried to query — so [] proves the early exit
    assert _graph_candidates(None, "the a an of to", "t", None) == []
    assert _graph_candidates(None, "", "t", None) == []
    assert _graph_candidates(None, "is it", "t", None) == []
