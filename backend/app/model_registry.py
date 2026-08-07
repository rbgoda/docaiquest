"""Centralized registry of every AI model operation in the platform.

One source of truth — every agent/service that currently reads
``os.getenv("DOCAIQ_*_MODEL")`` or hardcodes a model name should import from
here instead. The admin console reads the same registry so its Operations view
always matches reality.

Adding a new operation = adding one entry here + calling ``resolve_model()``
in the agent. The admin console picks it up automatically.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Literal

from app.config import get_settings

Category = Literal[
    "Vision / OCR",
    "Extraction & Classification",
    "Chat & Agent",
    "Embeddings",
    "Post-processing",
    "Local (no API key)",
]
Provider = Literal["dashscope", "google", "deepseek", "openrouter", "anthropic", "openai", "ollama", "local"]
CostTier = Literal["free", "cheap", "paid", "local"]


@dataclass(frozen=True)
class ModelOperation:
    """One AI operation —— its default model, provider, and how to configure it."""

    id: str               # unique key, e.g. "vision_ocr"
    label: str            # human-readable, e.g. "Vision Page OCR"
    category: Category
    default_model: str    # the fallback when neither env nor override is set
    env_var: str | None   # DOCAIQ_* env var that overrides the default (None = hardcoded)
    provider: Provider
    editable: bool        # can an admin override this operation's model?
    cost_tier: CostTier
    description: str      # one-liner for tooltips


@dataclass(frozen=True)
class ProviderInfo:
    """One AI provider and how its API key is sourced."""

    id: Provider
    label: str            # "DashScope (Alibaba)"
    env_key_attr: str     # Settings attribute name, e.g. "dashscope_api_key"
    docs_url: str         # where to sign up / get a key
    models: tuple[str, ...] = ()  # known model ids for this provider


# ── Registry ────────────────────────────────────────────────────────────────

REGISTRY: dict[str, ModelOperation] = {
    # ═══ Vision / OCR ═══════════════════════════════════════════════════════
    "vision_ocr": ModelOperation(
        id="vision_ocr",
        label="Vision Page OCR",
        category="Vision / OCR",
        default_model="google/gemini-2.5-flash",
        env_var=None,
        provider="google",
        editable=True,
        cost_tier="paid",
        description="Primary OCR cascade: FREE Gemini Flash → PAID Qwen-VL. Transcribes each page image to text.",
    ),
    "vision_markdown": ModelOperation(
        id="vision_markdown",
        label="Vision Markdown OCR",
        category="Vision / OCR",
        default_model="google/gemini-2.5-flash",
        env_var=None,
        provider="google",
        editable=True,
        cost_tier="paid",
        description="Structured markdown transcription for the Document Model path (GFM tables, headings). Same cascade as page OCR.",
    ),
    "vision_json_extract": ModelOperation(
        id="vision_json_extract",
        label="Vision JSON Extraction",
        category="Vision / OCR",
        default_model="google/gemini-2.5-flash",
        env_var=None,
        provider="google",
        editable=True,
        cost_tier="paid",
        description="Structured JSON extraction from page images (signatures, stamps, checkboxes, key fields).",
    ),
    "vision_figure_extract": ModelOperation(
        id="vision_figure_extract",
        label="Figure/Chart Extraction",
        category="Vision / OCR",
        default_model="google/gemini-2.5-flash",
        env_var=None,
        provider="google",
        editable=True,
        cost_tier="paid",
        description="Extract chart/graph data from pages containing figures. Same cascade as JSON extraction.",
    ),
    "vision_multipass_ocr": ModelOperation(
        id="vision_multipass_ocr",
        label="Multi-pass OCR (2nd opinion)",
        category="Vision / OCR",
        default_model="google/gemini-2.5-flash",
        env_var=None,
        provider="google",
        editable=True,
        cost_tier="paid",
        description="Second OCR pass on low-confidence pages using a different model. Gated by DOCAIQ_DOCUMENTS_MULTIPASS_OCR.",
    ),
    "vision_kyc": ModelOperation(
        id="vision_kyc",
        label="KYC Vision Extraction",
        category="Vision / OCR",
        default_model="google/gemini-2.5-flash",
        env_var=None,
        provider="google",
        editable=True,
        cost_tier="paid",
        description="KYC document vision cascade: Qwen-VL → Gemini → Claude. Audit product only (dormant in documents).",
    ),
    # ═══ Extraction & Classification ════════════════════════════════════════
    "classification": ModelOperation(
        id="classification",
        label="Document Classification",
        category="Extraction & Classification",
        default_model="anthropic/claude-haiku-4.5",
        env_var="DOCAIQ_CLASSIFIER_MODEL",
        provider="openrouter",
        editable=True,
        cost_tier="paid",
        description="Classifies each uploaded document into a type (invoice, bank statement, lab report, etc.).",
    ),
    "ner_extraction": ModelOperation(
        id="ner_extraction",
        label="NER Entity Extraction",
        category="Extraction & Classification",
        default_model="anthropic/claude-haiku-4.5",
        env_var="DOCAIQ_NER_MODEL",
        provider="openrouter",
        editable=True,
        cost_tier="paid",
        description="Named Entity Recognition: extracts people, orgs, locations, products from free text. Gated by DOCAIQ_NER_BACKEND.",
    ),
    "fact_extraction": ModelOperation(
        id="fact_extraction",
        label="Fact / Field Extraction",
        category="Extraction & Classification",
        default_model="dashscope/qwen-max",
        env_var="DOCAIQ_EXTRACTION_MODEL",
        provider="dashscope",
        editable=True,
        cost_tier="paid",
        description="Core extraction: reads the full document and populates structured fields per the schema. Temperature 0, tool-use.",
    ),
    "extraction_verify": ModelOperation(
        id="extraction_verify",
        label="Extraction Self-Verify",
        category="Extraction & Classification",
        default_model="google/gemini-2.5-flash",
        env_var=None,
        provider="google",
        editable=True,
        cost_tier="paid",
        description="Second pass that checks extraction completeness — fills in anything the first pass missed.",
    ),
    "strong_extraction": ModelOperation(
        id="strong_extraction",
        label="Strong Re-Extraction",
        category="Extraction & Classification",
        default_model="dashscope/qwen-max",
        env_var="DOCAIQ_STRONG_EXTRACT_MODEL",
        provider="dashscope",
        editable=True,
        cost_tier="paid",
        description="'Re-analyze with best model' action. Uses a stronger model for low-confidence docs.",
    ),
    "categorizer": ModelOperation(
        id="categorizer",
        label="Transaction Categorizer",
        category="Extraction & Classification",
        default_model="dashscope/qwen-max",
        env_var="DOCAIQ_DOCUMENTS_CATEGORIZE_MODEL",
        provider="dashscope",
        editable=True,
        cost_tier="paid",
        description="Categorizes transactions/merchants into standard accounting categories.",
    ),
    "schema_autopilot": ModelOperation(
        id="schema_autopilot",
        label="Schema Autopilot",
        category="Extraction & Classification",
        default_model="qwen-max",
        env_var="DOCAIQ_SCHEMA_AUTOPILOT_MODEL",
        provider="dashscope",
        editable=True,
        cost_tier="paid",
        description="Auto-drafts schemas for underserved docs. Falls back to strong_extract_model when env is empty.",
    ),
    "indexing_critic": ModelOperation(
        id="indexing_critic",
        label="Indexing Quality Critic",
        category="Extraction & Classification",
        default_model="dashscope/qwen-max",
        env_var=None,
        provider="dashscope",
        editable=True,
        cost_tier="paid",
        description="LLM evaluates chunk coherence, entity accuracy, and searchability after ingestion. Gated by DOCAIQ_DOCUMENTS_INDEXING_CRITIC.",
    ),
    # ═══ Chat & Agent ════════════════════════════════════════════════════════
    "chat_answer": ModelOperation(
        id="chat_answer",
        label="Chat Answer (RAG)",
        category="Chat & Agent",
        default_model="deepseek/deepseek-v4-flash",
        env_var=None,
        provider="deepseek",
        editable=True,
        cost_tier="cheap",
        description="Primary chat answer path: routes through the LLM routing config cascade (t1→t2→t3). Tenant-wide routing config.",
    ),
    "chat_intent_resolver": ModelOperation(
        id="chat_intent_resolver",
        label="Chat Intent Resolver",
        category="Chat & Agent",
        default_model="qwen-max",
        env_var="DOCAIQ_DOCUMENTS_GENERAL_FALLBACK_MODEL",
        provider="dashscope",
        editable=True,
        cost_tier="paid",
        description="Resolves chat intent: routes to deterministic handlers vs agent vs RAG. Falls back to strong_extract_model.",
    ),
    "chat_general_fallback": ModelOperation(
        id="chat_general_fallback",
        label="General Knowledge Fallback",
        category="Chat & Agent",
        default_model="qwen-max",
        env_var="DOCAIQ_DOCUMENTS_GENERAL_FALLBACK_MODEL",
        provider="dashscope",
        editable=True,
        cost_tier="paid",
        description="Answers off-topic/general-knowledge questions when no document evidence is found.",
    ),
    "chat_followup_rewrite": ModelOperation(
        id="chat_followup_rewrite",
        label="Follow-up Query Rewrite",
        category="Chat & Agent",
        default_model="deepseek/deepseek-v4-flash",
        env_var="DOCAIQ_DOCUMENTS_GENERAL_FALLBACK_MODEL",
        provider="deepseek",
        editable=True,
        cost_tier="paid",
        description="Contextualizes follow-up questions into standalone queries using chat history.",
    ),
    "chat_critic": ModelOperation(
        id="chat_critic",
        label="Answer Critic / Refine",
        category="Chat & Agent",
        default_model="qwen/qwen-2.5-7b-instruct",
        env_var="DOCAIQ_CRITIC_MODEL",
        provider="openrouter",
        editable=True,
        cost_tier="cheap",
        description="Self-corrects chat answers against source evidence. One cheap call per answer. Gated by DOCAIQ_DOCUMENTS_CRITIC_ENABLED.",
    ),
    "chat_guardrail": ModelOperation(
        id="chat_guardrail",
        label="Chat Guardrail",
        category="Chat & Agent",
        default_model="deepseek/deepseek-v4-flash",
        env_var=None,
        provider="deepseek",
        editable=True,
        cost_tier="cheap",
        description="Critiques answer grounding (input guard is regex, zero-LLM). Uses routing config cascade.",
    ),
    "chat_claim_verify": ModelOperation(
        id="chat_claim_verify",
        label="Claim Verification",
        category="Chat & Agent",
        default_model="deepseek/deepseek-v4-flash",
        env_var=None,
        provider="deepseek",
        editable=True,
        cost_tier="cheap",
        description="Per-claim faithfulness check. One extra LLM call per answer. Gated by DOCAIQ_CHAT_CLAIM_VERIFICATION.",
    ),
    "chat_crag": ModelOperation(
        id="chat_crag",
        label="Corrective RAG (CRAG)",
        category="Chat & Agent",
        default_model="deepseek/deepseek-v4-flash",
        env_var=None,
        provider="deepseek",
        editable=True,
        cost_tier="cheap",
        description="Query refinement on weak retrieval. Gated by DOCAIQ_CHAT_QUERY_ROUTING.",
    ),
    "chat_query_decompose": ModelOperation(
        id="chat_query_decompose",
        label="Multi-hop Query Decompose",
        category="Chat & Agent",
        default_model="deepseek/deepseek-v4-flash",
        env_var=None,
        provider="deepseek",
        editable=True,
        cost_tier="cheap",
        description="Breaks multi-hop questions into sub-questions. Uses routing config cascade.",
    ),
    # ═══ Embeddings ═════════════════════════════════════════════════════════
    "embed_v1_local": ModelOperation(
        id="embed_v1_local",
        label="Embeddings v1 (384d, local)",
        category="Embeddings",
        default_model="sentence-transformers/all-MiniLM-L6-v2",
        env_var="DOCAIQ_LOCAL_EMBED_MODEL",
        provider="local",
        editable=True,
        cost_tier="local",
        description="Default embedding model. Runs on CPU, no API key needed. 384-dimensional vectors.",
    ),
    "embed_v1_openai": ModelOperation(
        id="embed_v1_openai",
        label="Embeddings v1 (OpenAI)",
        category="Embeddings",
        default_model="text-embedding-3-small",
        env_var="DOCAIQ_OPENAI_EMBED_MODEL",
        provider="openai",
        editable=True,
        cost_tier="paid",
        description="OpenAI embeddings when DOCAIQ_EMBED_BACKEND=openai. Needs DOCAIQ_OPENAI_API_KEY.",
    ),
    "embed_v1_google": ModelOperation(
        id="embed_v1_google",
        label="Embeddings v1 (Gemini)",
        category="Embeddings",
        default_model="gemini-embedding-001",
        env_var=None,
        provider="google",
        editable=True,
        cost_tier="free",
        description="Gemini embeddings when DOCAIQ_EMBED_BACKEND=gemini. 1500 RPM free tier.",
    ),
    "embed_v1_dashscope": ModelOperation(
        id="embed_v1_dashscope",
        label="Embeddings v1 (DashScope)",
        category="Embeddings",
        default_model="text-embedding-v4",
        env_var="DOCAIQ_DASHSCOPE_EMBED_MODEL",
        provider="dashscope",
        editable=True,
        cost_tier="paid",
        description="DashScope embeddings when DOCAIQ_EMBED_BACKEND=dashscope. 1024d-native, CPU-friendly.",
    ),
    "embed_v2_local": ModelOperation(
        id="embed_v2_local",
        label="Embeddings v2 (1024d, BGE-M3)",
        category="Embeddings",
        default_model="BAAI/bge-m3",
        env_var="DOCAIQ_EMBED_V2_MODEL",
        provider="local",
        editable=True,
        cost_tier="local",
        description="Primary retrieval embedding. BGE-M3 (1024d, multilingual, 8192-ctx). Needs GPU for speed.",
    ),
    "embed_v2_dashscope": ModelOperation(
        id="embed_v2_dashscope",
        label="Embeddings v2 (DashScope)",
        category="Embeddings",
        default_model="text-embedding-v4",
        env_var="DOCAIQ_DASHSCOPE_EMBED_MODEL",
        provider="dashscope",
        editable=True,
        cost_tier="paid",
        description="DashScope v2 embeddings when DOCAIQ_EMBED_V2_BACKEND=dashscope.",
    ),
    # ═══ Post-processing ════════════════════════════════════════════════════
    "markdown_enhance": ModelOperation(
        id="markdown_enhance",
        label="Markdown Post-processing",
        category="Post-processing",
        default_model="deepseek-v4-flash",
        env_var=None,
        provider="deepseek",
        editable=True,
        cost_tier="cheap",
        description="DeepSeek V4 Flash cleans up OCR markdown (fix errors, normalize headings, align tables). Non-blocking — falls back to original.",
    ),
    "intelligence_proposals": ModelOperation(
        id="intelligence_proposals",
        label="AI View Proposals",
        category="Post-processing",
        default_model="dashscope/qwen-max",
        env_var="DOCAIQ_INTELLIGENCE_MODEL",
        provider="dashscope",
        editable=True,
        cost_tier="paid",
        description="Suggests dashboard views based on document corpus profile. One call triggered manually or nightly.",
    ),
    "contextual_retrieval": ModelOperation(
        id="contextual_retrieval",
        label="Contextual Retrieval",
        category="Post-processing",
        default_model="dashscope/qwen-turbo",
        env_var="DOCAIQ_CONTEXTUAL_MODEL",
        provider="dashscope",
        editable=True,
        cost_tier="cheap",
        description="Anthropic-style contextual retrieval: prepends chunk-context before embedding. High-volume (1 call per chunk + 1 per doc).",
    ),
    # ═══ Local (no API key) ═════════════════════════════════════════════════
    "reranker": ModelOperation(
        id="reranker",
        label="Reranker (Cross-encoder)",
        category="Local (no API key)",
        default_model="cross-encoder/ms-marco-MiniLM-L-6-v2",
        env_var="DOCAIQ_RERANKER_MODEL",
        provider="local",
        editable=True,
        cost_tier="local",
        description="Re-ranks retrieval candidates. CPU: MiniLM (fast, ~1.3s/query). GPU: bge-reranker-v2-m3 (568M, higher quality).",
    ),
    "docling_parser": ModelOperation(
        id="docling_parser",
        label="Docling Parser",
        category="Local (no API key)",
        default_model="docling (MIT)",
        env_var=None,
        provider="local",
        editable=True,
        cost_tier="local",
        description="Local PDF parser for multi-column layouts, complex tables, reading order. Gated by DOCAIQ_DOCUMENTS_DOCLING_ENABLED.",
    ),
}

# ── Providers ───────────────────────────────────────────────────────────────

PROVIDERS: dict[str, ProviderInfo] = {
    "dashscope": ProviderInfo(
        id="dashscope",
        label="DashScope (Alibaba)",
        env_key_attr="dashscope_api_key",
        docs_url="https://dashscope-intl.aliyuncs.com",
        models=("qwen-plus", "qwen-max", "qwen-turbo", "qwen-vl-max", "qwen3-vl-235b-a22b", "text-embedding-v4"),
    ),
    "google": ProviderInfo(
        id="google",
        label="Google Gemini",
        env_key_attr="google_genai_api_key",
        docs_url="https://aistudio.google.com/apikey",
        models=("gemini-flash-latest", "gemini-2.5-flash", "gemini-embedding-001"),
    ),
    "deepseek": ProviderInfo(
        id="deepseek",
        label="DeepSeek",
        env_key_attr="deepseek_api_key",
        docs_url="https://platform.deepseek.com",
        models=("deepseek-v4-flash", "deepseek-chat", "deepseek-reasoner"),
    ),
    "openrouter": ProviderInfo(
        id="openrouter",
        label="OpenRouter",
        env_key_attr="openrouter_api_key",
        docs_url="https://openrouter.ai/keys",
        models=(
            "anthropic/claude-haiku-4.5", "google/gemma-4-31b-it:free",
            "openai/gpt-oss-20b:free", "openai/gpt-oss-120b:free",
            "z-ai/glm-4.5-air:free", "qwen/qwen-2.5-7b-instruct",
            "qwen/qwen2.5-vl-72b-instruct",
        ),
    ),
    "anthropic": ProviderInfo(
        id="anthropic",
        label="Anthropic (Direct)",
        env_key_attr="anthropic_api_key",
        docs_url="https://console.anthropic.com/keys",
        models=("claude-haiku-4.5", "claude-sonnet-4-20250514"),
    ),
    "openai": ProviderInfo(
        id="openai",
        label="OpenAI",
        env_key_attr="openai_api_key",
        docs_url="https://platform.openai.com/api-keys",
        models=("text-embedding-3-small", "text-embedding-3-large"),
    ),
    "ollama": ProviderInfo(
        id="ollama",
        label="Ollama (Local)",
        env_key_attr="ollama_api_key",
        docs_url="https://ollama.com",
        models=(),
    ),
    "local": ProviderInfo(
        id="local",
        label="Local (CPU/GPU)",
        env_key_attr="",  # no API key
        docs_url="",
        models=(),
    ),
}

# ── Public API ──────────────────────────────────────────────────────────────

def resolve_model(op_id: str, overrides: dict | None = None) -> str:
    """Return the effective model for an operation.

    Resolution order: admin overrides → env var → hardcoded default.
    """
    op = REGISTRY.get(op_id)
    if op is None:
        raise KeyError(f"Unknown operation: {op_id!r}")

    # 1. Admin override (DB-stored)
    if overrides and op_id in overrides:
        override_model = overrides[op_id].get("model") if isinstance(overrides[op_id], dict) else overrides[op_id]
        if override_model:
            return override_model

    # 2. Env var
    if op.env_var:
        env_val = os.getenv(op.env_var, "").strip()
        if env_val:
            return env_val

    # 3. Hardcoded default
    return op.default_model


def get_operations_by_category(overrides: dict | None = None,
                               custom_providers: dict[str, dict] | None = None) -> list[dict]:
    """Return operations grouped by category, with effective models resolved.

    This is the canonical structure the admin console consumes.
    ``custom_providers`` slugs are passed to _effective_provider for proper
    provider-badge resolution on custom-provider model overrides.
    """
    settings = get_settings()
    custom_slugs = set(custom_providers or {})
    categories: dict[str, list[dict]] = {}
    for op in REGISTRY.values():
        model = resolve_model(op.id, overrides)
        provider = _effective_provider(model, op.provider, custom_slugs=custom_slugs)
        categories.setdefault(op.category, []).append({
            "id": op.id,
            "label": op.label,
            "model": model,
            "defaultModel": op.default_model,
            "envVar": op.env_var,
            "provider": provider,
            "editable": op.editable,
            "costTier": op.cost_tier,
            "description": op.description,
        })
    return [
        {"name": cat, "operations": ops}
        for cat, ops in categories.items()
    ]


def get_provider_models(custom: dict | None = None,
                        custom_providers: dict[str, dict] | None = None) -> dict[str, dict]:
    """Return each provider's model list, merged with admin custom entries.

    Resolution: DB custom models ∪ hardcoded defaults (deduplicated, order preserved).
    ``custom_providers`` is a dict of custom provider info from llm_admin
    (slug → {label, defaultModel, …}), appended after the hardcoded providers.
    """
    result: dict[str, dict] = {}
    custom = custom or {}
    for pid, pinfo in PROVIDERS.items():
        defaults = list(pinfo.models)
        extras = [m for m in custom.get(pid, []) if m not in defaults]
        merged = defaults + extras
        result[pid] = {
            "label": pinfo.label,
            "models": merged,
            "defaults": defaults,
            "custom": extras,
        }
    # Merge custom providers
    for slug, info in (custom_providers or {}).items():
        default_model = info.get("defaultModel")
        defaults = [default_model] if default_model else []
        extras = [m for m in custom.get(slug, []) if m not in defaults]
        result[slug] = {
            "label": info.get("label", slug),
            "models": defaults + extras,
            "defaults": defaults,
            "custom": extras,
        }
    return result


def get_providers_status(custom_providers: dict[str, dict] | None = None) -> dict[str, dict]:
    """Return each provider's key status for the admin console.
    ``custom_providers`` entries from llm_admin are appended to the result."""
    settings = get_settings()
    result: dict[str, dict] = {}
    for pid, pinfo in PROVIDERS.items():
        if pid == "local":
            result[pid] = {"hasKey": True, "keySource": "local", "label": pinfo.label}
            continue
        has_key = bool(getattr(settings, pinfo.env_key_attr, None))
        result[pid] = {
            "hasKey": has_key,
            "keySource": "env" if has_key else "none",
            "label": pinfo.label,
            "docsUrl": pinfo.docs_url,
        }
    # Merge custom providers
    for slug, info in (custom_providers or {}).items():
        ok = bool(info.get("enabled") and info.get("baseUrl"))
        result[slug] = {
            "hasKey": ok,
            "keySource": "custom" if ok else "none",
            "label": info.get("label", slug),
            "custom": True,
        }
    return result


def _effective_provider(model: str, default_provider: str,
                        custom_slugs: set | None = None) -> str:
    """Guess the provider from a model id, falling back to the default.
    ``custom_slugs`` are checked for prefix match after hardcoded providers."""
    if model.startswith("dashscope/"):
        return "dashscope"
    if model.startswith("openrouter/"):
        return "openrouter"
    if model.startswith("anthropic/"):
        return "anthropic"
    if model.startswith("google/"):
        return "google"
    if model.startswith("deepseek/"):
        return "deepseek"
    if model.startswith("ollama/"):
        return "ollama"
    if custom_slugs:
        for s in custom_slugs:
            if model.startswith(s + "/"):
                return s
    return default_provider
