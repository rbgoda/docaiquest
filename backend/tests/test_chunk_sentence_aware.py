"""RAG-roadmap #4 · sentence-aware oversized-block cutting.

`sentence_aware=False` must be byte-for-byte the old sliding window; `True` snaps
cuts to sentence boundaries. Pure, no deps.
"""
from __future__ import annotations

from app.chunking import _window, chunk_page_text


def test_window_off_is_the_old_sliding_window():
    text = "a" * 2500                      # no sentence boundaries → snap is a no-op anyway
    off = _window(text, 0, 1000, 150, None, False)
    assert [s for _t, s, _e in off] == [0, 850, 1700]   # step = target - overlap
    assert off[-1][2] == 2500


def test_window_sentence_aware_ends_on_a_boundary():
    para = "This is sentence one. " * 60   # ~1320 chars, > target
    segs = _window(para, 0, 1000, 150, None, True)
    assert len(segs) >= 2
    for seg_text, _s, _e in segs[:-1]:      # every non-final chunk ends at a sentence
        assert seg_text.rstrip().endswith(".")


def test_chunk_page_text_sentence_aware_valid_spans():
    text = "Alpha beta gamma delta epsilon. " * 100
    chunks = chunk_page_text(text, 1000, 150, sentence_aware=True)
    assert chunks and all(e > s for _t, s, e in chunks)
