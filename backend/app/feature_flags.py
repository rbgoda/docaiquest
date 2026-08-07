"""Feature flag resolution.

All feature toggles flow through a single function so the admin console
can override any flag at runtime (stored in routing_config DB, cached here).

Resolution order: admin DB override → env var → hardcoded default.
"""

from __future__ import annotations

import logging
import os as _os

log = logging.getLogger("docaiq.feature_flags")

# ── Cache ──────────────────────────────────────────────────────────────────
# Populated at boot (main.py lifespan) and invalidated on admin save.

_feature_flags_cache: dict = {}  # tenant_id → {flag_name: value}


def _read_feature_flags() -> dict:
    """Read cached feature flags for the current tenant. Returns {} on miss."""
    try:
        from app.db import get_current_tenant
        tid = get_current_tenant()
        if tid and tid in _feature_flags_cache:
            return _feature_flags_cache[tid]
    except Exception:
        pass
    return {}


def get_cached_feature_flags() -> dict:
    """Public reader — returns cached flags for current tenant, or {}."""
    return dict(_read_feature_flags())


def _refresh_feature_flags_cache(db) -> None:
    """Warm the feature-flags cache from DB. Called at boot + on admin save."""
    from app.db import get_current_tenant
    from app.repositories import routing_configs as rc_repo
    try:
        tid = get_current_tenant()
        rc = rc_repo.get(db)
        flags = (rc or {}).get("features", {}) if isinstance(rc, dict) else {}
        _feature_flags_cache[tid] = dict(flags) if isinstance(flags, dict) else {}
    except Exception:
        log.warning("feature_flags: cache warm failed (non-fatal)")


def invalidate_feature_flags_cache(tenant_id: str | None = None) -> None:
    """Clear the cache so the next call re-reads from DB."""
    if tenant_id:
        _feature_flags_cache.pop(tenant_id, None)
    else:
        _feature_flags_cache.clear()


# ── Public API ─────────────────────────────────────────────────────────────

def _read_env(name: str, env_var: str | None = None, *, prefix: str = "DOCAIQ_") -> str:
    """Read env var, trying: explicit env_var → DOCAIQ_<NAME> → bare <NAME>."""
    candidates = []
    if env_var:
        candidates.append(env_var)
    candidates.append(f"{prefix}{name.upper()}")
    candidates.append(name.upper())
    for c in candidates:
        val = _os.getenv(c, "").strip()
        if val:
            return val
    return ""


def is_enabled(name: str, default: bool = False, *, env_var: str | None = None) -> bool:
    """Return whether a feature flag is enabled.

    Resolution order:
    1. Admin DB override (routing_config.config.features)
    2. Env var (auto-resolved: explicit → DOCAIQ_<NAME> → <NAME>)
    3. ``default``
    """
    # 1. DB override
    flags = _read_feature_flags()
    if name in flags:
        val = flags[name]
        if isinstance(val, bool):
            return val
        if isinstance(val, str):
            return val.lower() in ("true", "1", "yes", "on")

    # 2. Env var
    env_val = _read_env(name, env_var)
    if env_val:
        return env_val.lower() in ("true", "1", "yes", "on")

    # 3. Default
    return default


def get_int(name: str, default: int = 0, *, env_var: str | None = None) -> int:
    """Return an integer config value. Same resolution order as is_enabled()."""
    # 1. DB override
    flags = _read_feature_flags()
    if name in flags:
        try:
            return int(flags[name])
        except (ValueError, TypeError):
            pass

    # 2. Env var
    env_val = _read_env(name, env_var)
    if env_val:
        try:
            return int(env_val)
        except ValueError:
            pass

    # 3. Default
    return default


def get_float(name: str, default: float = 0.0, *, env_var: str | None = None) -> float:
    """Return a float config value. Same resolution order as is_enabled()."""
    # 1. DB override
    flags = _read_feature_flags()
    if name in flags:
        try:
            return float(flags[name])
        except (ValueError, TypeError):
            pass

    # 2. Env var
    env_val = _read_env(name, env_var)
    if env_val:
        try:
            return float(env_val)
        except ValueError:
            pass

    # 3. Default
    return default


def get_str(name: str, default: str = "", *, env_var: str | None = None) -> str:
    """Return a string config value. Same resolution order as is_enabled()."""
    # 1. DB override
    flags = _read_feature_flags()
    if name in flags:
        val = flags[name]
        if isinstance(val, str) and val:
            return val

    # 2. Env var
    env_val = _read_env(name, env_var)
    if env_val:
        return env_val

    # 3. Default
    return default


# ── Canonical registry ─────────────────────────────────────────────────────
# Every flag the admin UI shows. Each entry: {name, label, category, type, default, help}.
# This is the single source of truth — the admin API reads from here.

FEATURE_FLAGS = [
    # ── Parsing & OCR ──────────────────────────────────────────────────────
    {"name": "documents_docling_enabled", "label": "Docling Parser", "category": "Parsing & OCR",
     "type": "bool", "default": False,
     "help": "IBM Docling parser for multi-column layouts, complex tables, reading order."},
    {"name": "vision_primary", "label": "Vision Primary Model", "category": "Parsing & OCR",
     "type": "str", "default": "gemini",
     "help": "Primary vision model for OCR cascade: 'gemini' (free-first) or 'qwen' (paid, more complete)."},
    {"name": "documents_multipass_ocr", "label": "Multi-pass OCR", "category": "Parsing & OCR",
     "type": "bool", "default": False,
     "help": "Second OCR pass on low-confidence pages using a different model."},
    {"name": "ocr_osd_autorotate", "label": "OCR Auto-rotate", "category": "Parsing & OCR",
     "type": "bool", "default": False,
     "help": "Auto-detect and correct page orientation before OCR (OSD)."},
    {"name": "documents_office_image_ocr", "label": "Office Image OCR", "category": "Parsing & OCR",
     "type": "bool", "default": False,
     "help": "Extract embedded images from DOCX/PPTX/XLSX and OCR them."},
    {"name": "documents_figure_extraction", "label": "Figure Extraction", "category": "Parsing & OCR",
     "type": "bool", "default": False,
     "help": "Extract charts, graphs, and figures from pages as structured data."},
    {"name": "documents_llm_bbox_fallback", "label": "LLM Bbox Fallback", "category": "Parsing & OCR",
     "type": "bool", "default": False,
     "help": "Use LLM to estimate bounding boxes when native parser bboxes are unavailable."},

    # ── Retrieval ──────────────────────────────────────────────────────────
    {"name": "reranker_enabled", "label": "Cross-encoder Reranker", "category": "Retrieval",
     "type": "bool", "default": False,
     "help": "Second-stage cross-encoder reranker after initial vector search."},
    {"name": "reranker_top_k_initial", "label": "Reranker Top-K (initial)", "category": "Retrieval",
     "type": "int", "default": 20,
     "help": "Number of candidates pulled from vector search before reranking."},
    {"name": "retrieval_recency_enabled", "label": "Recency Boost", "category": "Retrieval",
     "type": "bool", "default": False,
     "help": "Boost recent documents in retrieval results."},
    {"name": "retrieval_feedback_boost_enabled", "label": "Feedback Boost", "category": "Retrieval",
     "type": "bool", "default": False,
     "help": "Boost chunks that received positive user feedback in prior queries."},
    {"name": "retrieval_context_expansion", "label": "Context Expansion", "category": "Retrieval",
     "type": "bool", "default": False,
     "help": "Expand retrieved chunks with surrounding context window."},
    {"name": "retrieval_context_max_chars", "label": "Context Max Chars", "category": "Retrieval",
     "type": "int", "default": 2400,
     "help": "Max characters to include per chunk when context expansion is on."},
    {"name": "contextual_retrieval_enabled", "label": "Contextual Retrieval", "category": "Retrieval",
     "type": "bool", "default": True,
     "help": "Anthropic-style contextual chunk headers during ingestion."},
    {"name": "graph_retrieval_enabled", "label": "Graph Retrieval", "category": "Retrieval",
     "type": "bool", "default": True,
     "help": "Entity-graph-powered retrieval for name/org queries."},
    {"name": "retrieval_metrics_enabled", "label": "Retrieval Metrics", "category": "Retrieval",
     "type": "bool", "default": True,
     "help": "Record retrieval quality metrics (latency, hit rate) for analytics."},

    # ── Extraction ─────────────────────────────────────────────────────────
    {"name": "documents_extract_verify", "label": "Extraction Verify", "category": "Extraction",
     "type": "bool", "default": True,
     "help": "Second verification pass after extraction — fills in missed fields."},
    {"name": "documents_universal_extractor", "label": "Universal Extractor", "category": "Extraction",
     "type": "bool", "default": True,
     "help": "Schema-driven universal extraction pipeline (vs legacy per-type)."},
    {"name": "documents_indexing_critic", "label": "Indexing Quality Critic", "category": "Extraction",
     "type": "bool", "default": False,
     "help": "LLM evaluates chunk coherence, entity accuracy, and searchability after ingest."},

    # ── Chat & Agent ───────────────────────────────────────────────────────
    {"name": "documents_agentic_chat", "label": "Agentic Chat", "category": "Chat & Agent",
     "type": "bool", "default": True,
     "help": "Enable the ReAct tool-using agent for complex chat queries."},
    {"name": "documents_agent_fallback", "label": "Agent Fallback", "category": "Chat & Agent",
     "type": "bool", "default": True,
     "help": "Fall back to the workspace agent when deterministic handlers can't answer."},
    {"name": "documents_critic_enabled", "label": "Answer Critic", "category": "Chat & Agent",
     "type": "bool", "default": True,
     "help": "Self-correct chat answers against source evidence (one cheap call per answer)."},
    {"name": "documents_general_fallback_enabled", "label": "General Knowledge Fallback", "category": "Chat & Agent",
     "type": "bool", "default": True,
     "help": "Answer off-topic questions with general knowledge when no docs match."},
    {"name": "critic_max_refines", "label": "Max Critic Refines", "category": "Chat & Agent",
     "type": "int", "default": 1,
     "help": "Maximum refinement rounds the critic will attempt per answer."},

    # ── Limits ─────────────────────────────────────────────────────────────
    {"name": "documents_max_ocr_pages", "label": "Max OCR Pages", "category": "Limits",
     "type": "int", "default": 100,
     "help": "Maximum pages to OCR per document. Pages beyond this are skipped."},
    {"name": "documents_figure_max_pages", "label": "Max Figure Pages", "category": "Limits",
     "type": "int", "default": 8,
     "help": "Maximum pages to scan for figures/charts per document."},
    {"name": "documents_office_image_max", "label": "Max Office Images", "category": "Limits",
     "type": "int", "default": 12,
     "help": "Maximum embedded images to OCR per Office document."},
]


def get_all_flags() -> list[dict]:
    """Return the canonical flag list with current effective values merged in."""
    flags = _read_feature_flags()
    result = []
    for f in FEATURE_FLAGS:
        entry = dict(f)
        name = entry["name"]
        if name in flags:
            entry["value"] = flags[name]
        else:
            # Read current effective value
            if entry["type"] == "bool":
                entry["value"] = is_enabled(name, entry["default"])
            elif entry["type"] == "int":
                entry["value"] = get_int(name, entry["default"])
            elif entry["type"] == "float":
                entry["value"] = get_float(name, entry["default"])
            else:
                entry["value"] = get_str(name, entry["default"])
        result.append(entry)
    return result
