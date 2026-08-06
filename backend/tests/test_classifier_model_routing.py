"""Classifier model is provider-configurable (DOCAIQ_CLASSIFIER_MODEL), with the
default routing byte-identical to the legacy OpenRouter Claude-Haiku path.

Guards the prod-safety of the change: a bare/default model id must still route
via OpenRouter; only an explicit provider-prefixed override switches provider.
"""
from __future__ import annotations

import importlib

import pytest


@pytest.fixture()
def clf(monkeypatch):
    import app.agents.classifier as c
    return c


def _route(clf, model):
    # _routed_model() reads the module-level _MODEL; set it and call.
    import app.agents.classifier as c
    c._MODEL = model
    return c._routed_model(), c._routed_provider()


def test_default_routes_via_openrouter(clf):
    # Legacy default — MUST stay openrouter/anthropic/... so prod is unchanged.
    routed, prov = _route(clf, "anthropic/claude-haiku-4.5")
    assert routed == "openrouter/anthropic/claude-haiku-4.5"
    assert prov == "openrouter"


def test_bare_id_routes_via_openrouter(clf):
    routed, prov = _route(clf, "qwen-max")
    assert routed == "openrouter/qwen-max"
    assert prov == "openrouter"


def test_dashscope_prefix_routes_direct(clf):
    routed, prov = _route(clf, "dashscope/qwen-vl-max")
    assert routed == "dashscope/qwen-vl-max"
    assert prov == "dashscope"


def test_google_prefix_routes_direct(clf):
    routed, prov = _route(clf, "google/gemini-2.5-flash")
    assert routed == "google/gemini-2.5-flash"
    assert prov == "google"


def test_openrouter_prefix_unchanged(clf):
    routed, prov = _route(clf, "openrouter/anthropic/claude-haiku-4.5")
    assert routed == "openrouter/anthropic/claude-haiku-4.5"
    assert prov == "openrouter"


def test_empty_env_falls_back_to_default(monkeypatch):
    # The bug this guards: compose `${VAR:-}` sets the env to "" — `or` must
    # fall back to the default, not produce an empty model id.
    monkeypatch.setenv("DOCAIQ_CLASSIFIER_MODEL", "")
    import app.agents.classifier as c
    importlib.reload(c)
    assert c._MODEL == "anthropic/claude-haiku-4.5"
    assert c._routed_model() == "openrouter/anthropic/claude-haiku-4.5"


def test_env_override_applied(monkeypatch):
    monkeypatch.setenv("DOCAIQ_CLASSIFIER_MODEL", "dashscope/qwen-vl-max")
    import app.agents.classifier as c
    importlib.reload(c)
    assert c._MODEL == "dashscope/qwen-vl-max"
    assert c._routed_provider() == "dashscope"
