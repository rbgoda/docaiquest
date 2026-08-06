"""retrieval._rrf_fuse — Reciprocal Rank Fusion correctness (k=60).

Core to how hybrid BM25 + cosine ordering is produced; pure list-in/list-out.
"""
from __future__ import annotations

from app.retrieval import _RRF_K, _rrf_fuse


def test_empty_inputs():
    assert _rrf_fuse([], []) == []


def test_single_list_rank1_score():
    assert _rrf_fuse([(7, 0.9)], []) == [(7, 1.0 / (_RRF_K + 1))]


def test_dual_list_boost_beats_single_and_sums():
    # pk 1 is rank-1 in BOTH lists; pk 2 is only rank-2 in cosine
    out = dict(_rrf_fuse([(1, 0.5)], [(1, 0.5), (2, 0.9)]))
    assert out[1] > out[2]
    assert abs(out[1] - 2.0 / (_RRF_K + 1)) < 1e-9   # 1/61 + 1/61
    assert abs(out[2] - 1.0 / (_RRF_K + 2)) < 1e-9   # 1/62


def test_sorted_descending_and_cross_list_winner():
    # pk 3 is last in bm25 but first in cosine → its summed score wins
    out = _rrf_fuse([(1, 0.1), (2, 0.1), (3, 0.1)], [(3, 0.9)])
    scores = [s for _, s in out]
    assert scores == sorted(scores, reverse=True)
    assert out[0][0] == 3
