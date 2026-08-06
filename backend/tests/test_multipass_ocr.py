"""G11 · multi-pass OCR voting — the transcript-selection logic.

_best_transcript picks the highest G3-quality transcript among independent OCR
passes. Pure + deterministic, so we can test the voting without a VLM."""
from __future__ import annotations

from app.ingestion_vision import _best_transcript


def test_picks_higher_quality_transcript():
    # garbled (replacement chars / noise) vs a clean transcript → clean wins
    garbled = "Th� p�rt� �gr�� th�t �����"
    clean = "The parties agree that this Deed of Assignment is executed on 1 January 2026."
    assert _best_transcript([garbled, clean]) == clean
    assert _best_transcript([clean, garbled]) == clean


def test_ignores_empty_candidates():
    clean = "This is a perfectly legible sentence of transcribed text."
    assert _best_transcript(["", "   ", clean]) == clean


def test_all_empty_returns_empty():
    assert _best_transcript(["", "  ", None]) == ""


def test_single_candidate_passthrough():
    only = "The only available transcript from one OCR pass."
    assert _best_transcript([only]) == only
