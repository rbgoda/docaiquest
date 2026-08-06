"""Fresh-tenant LLM routing-config seeding.

`pick_default_for_seed` must return a key-appropriate cascade so a brand-new
tenant has a working router (no empty plan). DashScope is preferred (validated,
credit-reliable), then Google, then OpenRouter, else None.
"""
from __future__ import annotations

from app.llm import default_routing as dr


def _ids(cfg):
    return [m["id"] for t in cfg["tiers"] for m in t["models"]]


def test_dashscope_preferred_when_present():
    cfg = dr.pick_default_for_seed(dashscope_api_key="k")
    assert cfg is dr.DASHSCOPE_DIRECT_CASCADE
    assert all(i.startswith("dashscope/") for i in _ids(cfg))


def test_dashscope_wins_over_others():
    cfg = dr.pick_default_for_seed(
        dashscope_api_key="k", openrouter_api_key="o", google_api_key="g")
    assert cfg is dr.DASHSCOPE_DIRECT_CASCADE


def test_google_when_no_dashscope():
    cfg = dr.pick_default_for_seed(google_api_key="g", openrouter_api_key="o")
    assert cfg is dr.GEMINI_DIRECT_CASCADE


def test_openrouter_when_only_openrouter():
    cfg = dr.pick_default_for_seed(openrouter_api_key="o")
    assert cfg is dr.OPENROUTER_CASCADE


def test_none_when_no_keys():
    assert dr.pick_default_for_seed() is None


def test_cascade_shape_valid():
    cfg = dr.DASHSCOPE_DIRECT_CASCADE
    assert [t["id"] for t in cfg["tiers"]] == ["t1", "t2", "t3"]
    assert cfg["rules"] == []
    assert set(cfg["thresholds"]) >= {"autoApprove", "escalateT2", "escalateT3", "humanReview"}
