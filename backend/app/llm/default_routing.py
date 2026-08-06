"""Default routing-config templates per provider key situation.

The LLM gateway routes by prefix on the model `id` field:
  openrouter/...  → OpenRouter (one key, many models)
  anthropic/...   → Anthropic direct
  google/...      → Google GenAI direct
  (no prefix)     → stub fallback (canned response)

Historically the seed fixture shipped bare model IDs (`gemini-flash`,
`claude-haiku`) which fall through to the stub. That's appropriate for a
truly key-less dev box but surprises everyone the moment they add a real
OpenRouter key and don't realise the routing config needs updating too.

This module provides the right config for the keys we have. seed.py picks
the appropriate one at tenant-seed time; ensure_routing_config_in_sync()
re-validates on every backend boot so existing tenants get auto-upgraded
when a key gets added later.
"""

from __future__ import annotations

from typing import Any


# Free-tier OpenRouter cascade — same models the helper script writes.
# Picked because they're all on the free tier (Dec 2025 catalogue) so no
# billing required. Edit the list once you've added billing to mix paid
# models in (Sonnet on T3 for hard cases, etc).
OPENROUTER_CASCADE: dict[str, Any] = {
    "tiers": [
        {
            "id": "t1",
            "name": "Tier 1 · Cheap & Fast",
            "models": [
                {"id": "openrouter/google/gemma-4-31b-it:free", "name": "Gemma 4 31B", "provider": "Google", "cost": 0.0, "weight": 50, "status": "active", "calls7d": 0, "accuracy": 0.88},
                {"id": "openrouter/openai/gpt-oss-20b:free", "name": "GPT-OSS 20B", "provider": "OpenAI", "cost": 0.0, "weight": 50, "status": "active", "calls7d": 0, "accuracy": 0.87},
            ],
        },
        {
            "id": "t2",
            "name": "Tier 2 · Standard Reasoning",
            "models": [
                {"id": "dashscope/qwen-plus", "name": "Qwen Plus", "provider": "Alibaba (DashScope direct)", "cost": 0.4, "weight": 100, "status": "active", "calls7d": 0, "accuracy": 0.94},
            ],
        },
        {
            "id": "t3",
            "name": "Tier 3 · Premium Reasoning",
            "models": [
                {"id": "openrouter/openai/gpt-oss-120b:free", "name": "GPT-OSS 120B", "provider": "OpenAI", "cost": 0.0, "weight": 50, "status": "active", "calls7d": 0, "accuracy": 0.94},
                {"id": "openrouter/z-ai/glm-4.5-air:free", "name": "GLM 4.5 Air", "provider": "Z-AI", "cost": 0.0, "weight": 50, "status": "active", "calls7d": 0, "accuracy": 0.93},
            ],
        },
    ],
    "thresholds": {
        "autoApprove": 0.85,
        "escalateT2": 0.75,
        "escalateT3": 0.60,
        "humanReview": 0.50,
    },
    "rules": [],
}


# M46 · Documents product · RELIABLE cascade. The free models (Gemini free tier,
# OpenRouter :free) 429 under any real load and silently break chat / summary /
# markdown. Documents puts the same PAID model the extractor uses
# (anthropic/claude-haiku-4.5 via OpenRouter) at T1+T2 so those features are
# dependable; a free model is the last-ditch T3.
DOCUMENTS_RELIABLE_CASCADE: dict[str, Any] = {
    "tiers": [
        {
            "id": "t1",
            "name": "Tier 1 · Reliable (Qwen-Plus · DashScope)",
            "models": [
                # DashScope/qwen is the funded, reliable path (OpenRouter/Claude credits deplete
                # and 402 — which surfaced as "assistant temporarily unavailable" in chat).
                # qwen-plus is the primary: ~4x cheaper than qwen-max and as good for RAG answers.
                {"id": "dashscope/qwen-plus", "name": "Qwen-Plus", "provider": "Alibaba", "cost": 0.5, "weight": 100, "status": "active", "calls7d": 0, "accuracy": 0.94},
                {"id": "openrouter/anthropic/claude-haiku-4.5", "name": "Claude Haiku 4.5", "provider": "Anthropic", "cost": 1.0, "weight": 50, "status": "active", "calls7d": 0, "accuracy": 0.95},
            ],
        },
        {
            "id": "t2",
            "name": "Tier 2 · Reliable (Qwen-Max · DashScope)",
            "models": [
                # qwen-max is the stronger fallback for hard cross-doc reasoning.
                {"id": "dashscope/qwen-max", "name": "Qwen-Max", "provider": "Alibaba", "cost": 1.0, "weight": 100, "status": "active", "calls7d": 0, "accuracy": 0.95},
                {"id": "openrouter/anthropic/claude-haiku-4.5", "name": "Claude Haiku 4.5", "provider": "Anthropic", "cost": 1.0, "weight": 50, "status": "active", "calls7d": 0, "accuracy": 0.95},
            ],
        },
        {
            "id": "t3",
            "name": "Tier 3 · Free fallback",
            "models": [
                {"id": "openrouter/openai/gpt-oss-120b:free", "name": "GPT-OSS 120B", "provider": "OpenAI", "cost": 0.0, "weight": 100, "status": "active", "calls7d": 0, "accuracy": 0.94},
            ],
        },
    ],
    "thresholds": {
        "autoApprove": 0.85,
        "escalateT2": 0.75,
        "escalateT3": 0.60,
        "humanReview": 0.50,
    },
    "rules": [],
}


# M31.7 · Gemini-direct cascade — uses google/* model ids so the gateway
# routes via _google() instead of OpenRouter. Free tier: 1500 RPM for
# Flash, 15 RPM for Pro — way more headroom than OpenRouter's per-model
# 5-RPM free tier. Preferred when DOCAIQ_GOOGLE_GENAI_API_KEY is set.
GEMINI_DIRECT_CASCADE: dict[str, Any] = {
    "tiers": [
        {
            "id": "t1",
            "name": "Tier 1 · Gemini 2.5 Flash",
            "models": [
                {"id": "google/gemini-2.5-flash", "name": "Gemini 2.5 Flash", "provider": "Google", "cost": 0.0, "weight": 100, "status": "active", "calls7d": 0, "accuracy": 0.93},
            ],
        },
        {
            "id": "t2",
            "name": "Tier 2 · Gemini Flash (latest alias · same RPM bucket)",
            "models": [
                # 2.5-flash for T2 too — single 429 doesn't escalate to a
                # different model immediately. Escalate on CONFIDENCE, not
                # transient errors.
                {"id": "google/gemini-2.5-flash", "name": "Gemini 2.5 Flash", "provider": "Google", "cost": 0.0, "weight": 100, "status": "active", "calls7d": 0, "accuracy": 0.93},
            ],
        },
        {
            "id": "t3",
            "name": "Tier 3 · OpenRouter fallback (when Gemini RPD hit)",
            "models": [
                # When Gemini's daily quota (20 RPD per model free tier)
                # is exhausted, fall through to OpenRouter so the matcher
                # keeps working until midnight quota reset. GPT-OSS-120B
                # is a decent fallback; Gemma 4 is faster.
                {"id": "openrouter/openai/gpt-oss-120b:free", "name": "GPT-OSS 120B (OR fallback)", "provider": "OpenAI", "cost": 0.0, "weight": 50, "status": "active", "calls7d": 0, "accuracy": 0.94},
                {"id": "openrouter/google/gemma-4-31b-it:free", "name": "Gemma 4 31B (OR fallback)", "provider": "Google", "cost": 0.0, "weight": 50, "status": "active", "calls7d": 0, "accuracy": 0.88},
            ],
        },
    ],
    "thresholds": {
        "autoApprove": 0.85,
        "escalateT2": 0.75,
        "escalateT3": 0.60,
        "humanReview": 0.50,
    },
    "rules": [],
}


# DashScope-direct cascade — the validated, credit-reliable path (qwen via the
# DashScope OpenAI-compatible endpoint). Preferred for a fresh tenant that has a
# DashScope key, since it doesn't depend on OpenRouter credit or free-tier RPM.
DASHSCOPE_DIRECT_CASCADE: dict[str, Any] = {
    "tiers": [
        {
            "id": "t1",
            "name": "Tier 1 · Qwen Plus (DashScope)",
            "models": [
                {"id": "dashscope/qwen-plus", "name": "Qwen Plus", "provider": "Alibaba (DashScope direct)", "cost": 0.4, "weight": 100, "status": "active", "calls7d": 0, "accuracy": 0.94},
            ],
        },
        {
            "id": "t2",
            "name": "Tier 2 · Qwen Plus (DashScope)",
            "models": [
                {"id": "dashscope/qwen-plus", "name": "Qwen Plus", "provider": "Alibaba (DashScope direct)", "cost": 0.4, "weight": 100, "status": "active", "calls7d": 0, "accuracy": 0.94},
            ],
        },
        {
            "id": "t3",
            "name": "Tier 3 · Qwen Max (DashScope)",
            "models": [
                {"id": "dashscope/qwen-max", "name": "Qwen Max", "provider": "Alibaba (DashScope direct)", "cost": 1.6, "weight": 100, "status": "active", "calls7d": 0, "accuracy": 0.96},
            ],
        },
    ],
    "thresholds": {
        "autoApprove": 0.85,
        "escalateT2": 0.75,
        "escalateT3": 0.60,
        "humanReview": 0.50,
    },
    "rules": [],
}


def _config_has_openrouter_prefix(cfg: dict | None) -> bool:
    """True if any model in the config already uses an openrouter/ prefix.
    Used by the boot-time updater to skip already-migrated tenants."""
    if not cfg:
        return False
    for tier in cfg.get("tiers", []):
        for model in tier.get("models", []):
            model_id = (model or {}).get("id", "")
            if isinstance(model_id, str) and model_id.startswith("openrouter/"):
                return True
    return False


def _config_has_bare_ids(cfg: dict | None) -> bool:
    """True if the config has at least one model with NO provider prefix.
    These are the legacy 'gemini-flash' / 'claude-haiku' rows that fall
    through to the stub and need upgrading."""
    if not cfg:
        return False
    for tier in cfg.get("tiers", []):
        for model in tier.get("models", []):
            model_id = (model or {}).get("id", "") or ""
            if "/" not in model_id and model_id:
                return True
    return False


def pick_default_for_seed(
    *, openrouter_api_key: str = "", google_api_key: str = "", dashscope_api_key: str = ""
) -> dict | None:
    """Returns the routing-config dict appropriate for the current key
    situation, OR None to fall back to the JSON fixture on disk.

    Preference order (when multiple keys are present):
      1. DashScope direct — the validated, credit-reliable path; doesn't depend
         on OpenRouter credit or free-tier RPM throttling.
      2. Google Gemini direct — 1500 RPM free tier on Flash.
      3. OpenRouter cascade — covers many models via one key.
      4. Stub fallback (JSON fixture) when no keys set.
    """
    if dashscope_api_key:
        return DASHSCOPE_DIRECT_CASCADE
    if google_api_key:
        return GEMINI_DIRECT_CASCADE
    if openrouter_api_key:
        return OPENROUTER_CASCADE
    return None
