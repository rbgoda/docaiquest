"""Recency-weighted retrieval — the time-decay multiplier (pure, no DB)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.retrieval import _recency_factor

NOW = datetime(2026, 6, 23, tzinfo=timezone.utc)
HL = 180.0
FLOOR = 0.5


def _f(days):
    return _recency_factor(NOW - timedelta(days=days), NOW, HL, FLOOR)


def test_now_is_full_weight():
    assert _f(0) == 1.0


def test_half_life_is_midpoint():
    # floor + (1-floor)*0.5 = 0.75
    assert round(_f(HL), 3) == 0.75


def test_decays_monotonically():
    assert _f(0) > _f(90) > _f(180) > _f(360) > _f(1000)


def test_floor_never_buries_old_docs():
    # very old still >= floor, never 0
    assert FLOOR <= _f(10000) < FLOOR + 0.01


def test_unknown_date_is_neutral():
    assert _recency_factor(None, NOW, HL, FLOOR) == 1.0


def test_naive_datetime_does_not_crash():
    assert _recency_factor(datetime(2026, 6, 23), NOW, HL, FLOOR) > 0


def test_floor_zero_decays_to_zero():
    assert round(_recency_factor(NOW - timedelta(days=10000), NOW, HL, 0.0), 3) == 0.0
