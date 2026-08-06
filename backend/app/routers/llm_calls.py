"""LLM-call ledger reads — drives the WhyModal in Review.jsx."""

from __future__ import annotations

import logging
import time

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_current_tenant, get_session
from app.llm.gateway import available_backends
from app.orm import LLMCall
from app.security import CurrentUser, require_role

log = logging.getLogger("docaiq.llm")

router = APIRouter()


class LLMCallDTO(BaseModel):
    pk: int
    tier: str
    provider: str
    model: str
    inputTokens: int
    outputTokens: int
    costUsd: float
    latencyMs: int
    confidence: float | None = None
    status: str
    error: str | None = None
    createdAt: str


class LLMTraceResponse(BaseModel):
    chatMessagePk: int
    calls: list[LLMCallDTO]
    totalCostUsd: float
    totalLatencyMs: int
    totalTokens: int


@router.get("/llm-calls/by-message/{message_pk}", response_model=LLMTraceResponse)
def trace_for_message(message_pk: int, db: Session = Depends(get_session)) -> dict:
    # P2 · cloud-only
    from app.license import is_cloud
    if not is_cloud():
        raise HTTPException(status_code=403, detail={
            "code": "cloud_feature",
            "message": "LLM cost analytics is a DocAIQuest Cloud feature — not available on this OSS deployment.",
        })
    tid = get_current_tenant()
    rows = db.scalars(
        select(LLMCall)
        .where(LLMCall.tenant_id == tid, LLMCall.chat_message_pk == message_pk)
        .order_by(LLMCall.pk)
    ).all()
    calls = [
        {
            "pk": r.pk, "tier": r.tier, "provider": r.provider, "model": r.model,
            "inputTokens": r.input_tokens, "outputTokens": r.output_tokens,
            "costUsd": r.cost_usd, "latencyMs": r.latency_ms,
            "confidence": r.confidence, "status": r.status, "error": r.error,
            "createdAt": r.created_at.isoformat() if r.created_at else "",
        }
        for r in rows
    ]
    return {
        "chatMessagePk": message_pk,
        "calls": calls,
        "totalCostUsd": sum(c["costUsd"] for c in calls),
        "totalLatencyMs": sum(c["latencyMs"] for c in calls),
        "totalTokens": sum(c["inputTokens"] + c["outputTokens"] for c in calls),
    }


# ---- Provider availability --------------------------------------------------
# Tells the UI which model-id prefixes will actually resolve to a backend
# vs which fall through to the stub for lack of an API key. Used by the
# "+Add model" form on the Routing admin screen so the admin sees that
# `openrouter/*` works while `anthropic/*` needs an env var.

class ProviderInfo(BaseModel):
    prefix: str                 # e.g. "openrouter"
    label: str                  # human-friendly: "OpenRouter"
    configured: bool            # True iff API key is set
    envVar: str                 # the env var name to set if unconfigured
    exampleIds: list[str]       # 2-3 model ids that work with this prefix
    docsUrl: str | None = None  # where to find model lists


_PROVIDER_META = {
    "openrouter": {
        "label": "OpenRouter",
        "envVar": "DOCAIQ_OPENROUTER_API_KEY",
        "exampleIds": [
            "openrouter/google/gemini-2.0-flash-exp:free",
            "openrouter/anthropic/claude-3.5-sonnet",
            "openrouter/deepseek/deepseek-chat",
        ],
        "docsUrl": "https://openrouter.ai/models",
    },
    "anthropic": {
        "label": "Anthropic (direct)",
        "envVar": "DOCAIQ_ANTHROPIC_API_KEY",
        "exampleIds": [
            "anthropic/claude-3-5-haiku-latest",
            "anthropic/claude-3-5-sonnet-latest",
            "anthropic/claude-opus-4",
        ],
        "docsUrl": "https://docs.anthropic.com/en/docs/about-claude/models",
    },
    "google": {
        "label": "Google AI Studio (direct)",
        "envVar": "DOCAIQ_GOOGLE_GENAI_API_KEY",
        "exampleIds": [
            "google/gemini-2.0-flash-exp",
            "google/gemini-1.5-pro-latest",
        ],
        "docsUrl": "https://ai.google.dev/gemini-api/docs/models/gemini",
    },
    "dashscope": {
        "label": "Alibaba DashScope (Qwen direct)",
        "envVar": "DOCAIQ_DASHSCOPE_API_KEY",
        "exampleIds": [
            "dashscope/qwen-plus",
            "dashscope/qwen-max",
            "dashscope/qwen-turbo",
        ],
        "docsUrl": "https://www.alibabacloud.com/help/en/model-studio/getting-started/models",
    },
    "ollama": {
        "label": "Ollama (local)",
        "envVar": "DOCAIQ_OLLAMA_BASE_URL",
        "exampleIds": [
            "ollama/llama3.2",
            "ollama/mistral",
            "ollama/phi4",
        ],
        "docsUrl": "https://ollama.com/search",
    },
}


@router.get("/llm/providers", response_model=list[ProviderInfo])
def list_providers(
    _user: CurrentUser = Depends(require_role("admin")),
    db: Session = Depends(get_session),
) -> list[dict]:
    """Which LLM backend prefixes are wired and ready to take traffic.
    `configured=false` means `_resolve_backend` will fall through to the
    stub for any model id with that prefix — admin needs to set the env
    var to make new models of that family actually work."""
    backends = available_backends()
    out = []
    for prefix, meta in _PROVIDER_META.items():
        out.append({
            "prefix": prefix,
            "label": meta["label"],
            "configured": backends.get(prefix, False),
            "envVar": meta["envVar"],
            "exampleIds": meta["exampleIds"],
            "docsUrl": meta["docsUrl"],
        })
    # Merge custom (OpenAI-compatible) providers registered by superadmin
    try:
        from app import llm_admin
        for cp in llm_admin.list_custom_providers(db):
            out.append({
                "prefix": cp["provider"],
                "label": cp.get("label", cp["provider"]),
                "configured": cp.get("configured", False),
                "envVar": "",
                "exampleIds": [],
                "docsUrl": cp.get("baseUrl") or "",
            })
    except Exception:  # noqa: BLE001
        pass
    return out
    return out


# ---- OpenRouter catalog browser --------------------------------------------
# Lets the admin pick from OpenRouter's live model list instead of typing
# ids by hand. Cached in-process ~10 min — OpenRouter's catalog changes
# slowly and we don't want to slam their API every time someone opens the
# +Add model modal. Memory-only cache (no Redis dep), per-process; that's
# fine because the catalog is identical across tenants and processes.

class CatalogEntry(BaseModel):
    id: str                       # full prefixed id ready for routing_config (e.g. "openrouter/qwen/qwen-2.5-72b-instruct")
    rawId: str                    # OpenRouter's own id (without the prefix)
    name: str                     # human-readable model name
    contextLength: int | None = None
    promptCostPer1MUsd: float | None = None    # input tokens
    completionCostPer1MUsd: float | None = None  # output tokens
    typicalCostPer1MUsd: float | None = None   # what to surface as "cost" in routing_config
    free: bool = False            # true when both costs are 0


_catalog_cache: dict = {"at": 0.0, "data": None}
_CATALOG_TTL_SEC = 600
_OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"


@router.get("/llm/openrouter-catalog", response_model=list[CatalogEntry])
async def openrouter_catalog(
    _user: CurrentUser = Depends(require_role("admin")),
) -> list[dict]:
    """Live OpenRouter model list. Cached ~10 min per backend process.

    Normalises pricing — OpenRouter reports `$/token` as decimal strings
    (e.g. "0.00000015"). We convert to `$/1M tokens` for parity with our
    routing_config schema. The `typicalCostPer1MUsd` is a weighted blend
    (1:3 prompt:completion, since completions dominate most flows) — close
    enough as a default the admin can override later."""
    now = time.time()
    if _catalog_cache["data"] is not None and (now - _catalog_cache["at"]) < _CATALOG_TTL_SEC:
        return _catalog_cache["data"]

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(_OPENROUTER_MODELS_URL)
    except httpx.HTTPError as e:
        # Cache miss + network failure — return whatever we had last, or fail.
        if _catalog_cache["data"] is not None:
            return _catalog_cache["data"]
        raise HTTPException(status_code=502, detail=f"OpenRouter unreachable: {e}")
    if resp.status_code >= 400:
        if _catalog_cache["data"] is not None:
            return _catalog_cache["data"]
        raise HTTPException(status_code=502, detail=f"OpenRouter returned HTTP {resp.status_code}")

    raw = resp.json().get("data", []) or []
    out: list[dict] = []
    for m in raw:
        rid = m.get("id")
        if not rid:
            continue
        # Pricing strings on OpenRouter come as decimal-per-token, e.g.
        # "0.00000015" for $0.15 per 1M. Some entries are missing — guard.
        pricing = m.get("pricing", {}) or {}
        try:
            prompt_per_1m = float(pricing.get("prompt", 0)) * 1_000_000 if pricing.get("prompt") else None
        except (TypeError, ValueError):
            prompt_per_1m = None
        try:
            completion_per_1m = float(pricing.get("completion", 0)) * 1_000_000 if pricing.get("completion") else None
        except (TypeError, ValueError):
            completion_per_1m = None
        # 1:3 blend — completion dominates most flows. Falls back gracefully
        # when either side is missing.
        if prompt_per_1m is not None and completion_per_1m is not None:
            typical = (prompt_per_1m + 3 * completion_per_1m) / 4
        elif completion_per_1m is not None:
            typical = completion_per_1m
        elif prompt_per_1m is not None:
            typical = prompt_per_1m
        else:
            typical = None

        out.append({
            "id": f"openrouter/{rid}",
            "rawId": rid,
            "name": m.get("name") or rid,
            "contextLength": m.get("context_length"),
            "promptCostPer1MUsd": round(prompt_per_1m, 4) if prompt_per_1m is not None else None,
            "completionCostPer1MUsd": round(completion_per_1m, 4) if completion_per_1m is not None else None,
            "typicalCostPer1MUsd": round(typical, 4) if typical is not None else None,
            "free": (prompt_per_1m == 0 and completion_per_1m == 0) if (prompt_per_1m is not None and completion_per_1m is not None) else rid.endswith(":free"),
        })
    # Sort: free first (handy for cost-conscious tier 1), then by name.
    out.sort(key=lambda e: (not e["free"], e["name"].lower()))
    _catalog_cache.update({"at": now, "data": out})
    log.info("openrouter catalog refreshed · %d models", len(out))
    return out
