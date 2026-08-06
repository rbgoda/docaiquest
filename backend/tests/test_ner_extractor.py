"""General-purpose LLM NER (Foundation-Fix-B).

Covers the extractor's guarantees WITHOUT hitting a provider: model routing, and
the grounding / dedup / kind-filter / canonicalization applied to a mocked LLM
response. Grounding is the safety property — a hallucinated span (not present in
the source text) must be dropped.
"""
from __future__ import annotations

import importlib
import json

import pytest


@pytest.fixture()
def ner(monkeypatch):
    import app.agents.ner_extractor as n
    # A key must appear present or the extractor short-circuits before the call.
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "openrouter_api_key", "test-key", raising=False)
    return n


def _fake_result(entities, module, relations=None):
    """Build a gateway.CompletionResult whose text is the NER JSON payload."""
    from app.llm import gateway
    payload = {"entities": entities}
    if relations is not None:
        payload["relations"] = relations
    return gateway.CompletionResult(
        text=json.dumps(payload),
        model="anthropic/claude-haiku-4.5",
        provider="openrouter",
        input_tokens=100,
        output_tokens=50,
        latency_ms=42,
    )


# ── Routing (mirrors the classifier's provider routing) ────────────────────
def test_default_routes_via_openrouter():
    import app.agents.ner_extractor as n
    n._MODEL = "anthropic/claude-haiku-4.5"
    assert n._routed_model() == "openrouter/anthropic/claude-haiku-4.5"
    assert n._routed_provider() == "openrouter"


def test_dashscope_prefix_routes_direct():
    import app.agents.ner_extractor as n
    n._MODEL = "dashscope/qwen-max"
    assert n._routed_model() == "dashscope/qwen-max"
    assert n._routed_provider() == "dashscope"


def test_empty_env_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("DOCAIQ_NER_MODEL", "")
    import app.agents.ner_extractor as n
    importlib.reload(n)
    assert n._MODEL == "anthropic/claude-haiku-4.5"


# ── Grounding / dedup / filtering ──────────────────────────────────────────
def test_grounded_entities_kept_ungrounded_dropped(ner, monkeypatch):
    blocks = [
        (1, "Jane Doe signed the agreement on behalf of Acme Pte Ltd."),
        (2, "The service is governed by the laws of Singapore."),
    ]
    llm_entities = [
        {"kind": "person", "text": "Jane Doe", "confidence": 0.95},       # grounded p1
        {"kind": "org", "text": "Acme Pte Ltd", "confidence": 0.9},       # grounded p1
        {"kind": "location", "text": "Singapore", "confidence": 0.8},     # grounded p2
        {"kind": "person", "text": "John Smith", "confidence": 0.7},      # NOT in source → drop
    ]
    monkeypatch.setattr(ner.gateway, "call",
                        lambda **kw: _fake_result(llm_entities, ner))

    ents, telemetry = ner.extract_entities_llm(blocks)
    assert telemetry["status"] == "ok"
    texts = {e.text for e in ents}
    assert texts == {"Jane Doe", "Acme Pte Ltd", "Singapore"}   # hallucination dropped
    # Page attribution: Singapore came from page 2.
    assert next(e for e in ents if e.text == "Singapore").page == 2
    assert next(e for e in ents if e.text == "Jane Doe").page == 1


def test_unknown_kind_filtered(ner, monkeypatch):
    blocks = [(1, "Acme Pte Ltd is based in Berlin.")]
    llm_entities = [
        {"kind": "org", "text": "Acme Pte Ltd", "confidence": 0.9},
        {"kind": "spaceship", "text": "Berlin", "confidence": 0.9},  # invalid kind → drop
    ]
    monkeypatch.setattr(ner.gateway, "call",
                        lambda **kw: _fake_result(llm_entities, ner))
    ents, _ = ner.extract_entities_llm(blocks)
    assert [e.kind for e in ents] == ["org"]


def test_dedup_by_canonical(ner, monkeypatch):
    # "Acme Pte Ltd" and "ACME" canonicalize to the same org → one entity.
    blocks = [(1, "Acme Pte Ltd trades as ACME in the region.")]
    llm_entities = [
        {"kind": "org", "text": "Acme Pte Ltd", "confidence": 0.9},
        {"kind": "org", "text": "ACME", "confidence": 0.6},
    ]
    monkeypatch.setattr(ner.gateway, "call",
                        lambda **kw: _fake_result(llm_entities, ner))
    ents, _ = ner.extract_entities_llm(blocks)
    orgs = [e for e in ents if e.kind == "org"]
    assert len(orgs) == 1  # canon_org("Acme Pte Ltd") == canon_org("ACME") == "acme"
    assert orgs[0].canonical == "acme"


def test_no_api_key_skips_gracefully(monkeypatch):
    import app.agents.ner_extractor as n
    from app.config import get_settings
    s = get_settings()
    monkeypatch.setattr(s, "openrouter_api_key", "", raising=False)
    n._MODEL = "anthropic/claude-haiku-4.5"  # routes to openrouter
    ents, telemetry = n.extract_entities_llm([(1, "some text")])
    assert ents == []
    assert telemetry["error"] == "no_api_key"


def test_empty_input_is_noop(ner):
    ents, telemetry = ner.extract_entities_llm([(1, "  "), (2, "")])
    assert ents == []
    assert telemetry["status"] == "ok"


# ── B3 · relations ─────────────────────────────────────────────────────────
def test_relation_between_grounded_entities_kept(ner, monkeypatch):
    blocks = [(1, "Jane Doe works for Acme Pte Ltd in Singapore.")]
    entities = [
        {"kind": "person", "text": "Jane Doe", "confidence": 0.95},
        {"kind": "org", "text": "Acme Pte Ltd", "confidence": 0.9},
    ]
    relations = [
        {"src": "Jane Doe", "dst": "Acme Pte Ltd", "relation": "Works For", "confidence": 0.85},
    ]
    monkeypatch.setattr(ner.gateway, "call",
                        lambda **kw: _fake_result(entities, ner, relations))
    ents, rels, telemetry = ner.extract_graph_llm(blocks)
    assert telemetry["status"] == "ok"
    assert len(rels) == 1
    r = rels[0]
    assert (r.src, r.dst) == ("Jane Doe", "Acme Pte Ltd")
    assert r.relation == "works_for"          # slug normalised from "Works For"


def test_relation_with_ungrounded_endpoint_dropped(ner, monkeypatch):
    blocks = [(1, "Jane Doe works for Acme Pte Ltd.")]
    entities = [
        {"kind": "person", "text": "Jane Doe", "confidence": 0.95},
        {"kind": "org", "text": "Acme Pte Ltd", "confidence": 0.9},
    ]
    relations = [
        # dst "Globex" is never an extracted/grounded entity → drop the edge.
        {"src": "Jane Doe", "dst": "Globex", "relation": "works_for", "confidence": 0.8},
        # self-loop → drop.
        {"src": "Jane Doe", "dst": "Jane Doe", "relation": "is", "confidence": 0.5},
    ]
    monkeypatch.setattr(ner.gateway, "call",
                        lambda **kw: _fake_result(entities, ner, relations))
    _ents, rels, _t = ner.extract_graph_llm(blocks)
    assert rels == []


def test_entities_only_wrapper_still_two_tuple(ner, monkeypatch):
    # Back-compat: extract_entities_llm keeps its (entities, telemetry) shape.
    blocks = [(1, "Acme Pte Ltd operates here.")]
    monkeypatch.setattr(ner.gateway, "call", lambda **kw: _fake_result(
        [{"kind": "org", "text": "Acme Pte Ltd", "confidence": 0.9}], ner,
        [{"src": "Acme Pte Ltd", "dst": "Acme Pte Ltd", "relation": "x"}]))
    result = ner.extract_entities_llm(blocks)
    assert len(result) == 2  # (entities, telemetry) — relations not exposed here
    ents, telemetry = result
    assert [e.kind for e in ents] == ["org"]
