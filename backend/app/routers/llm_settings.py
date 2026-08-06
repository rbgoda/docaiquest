"""User-facing LLM provider settings (OSS). Lets each user configure their own
API keys and model preferences without touching env vars or the admin console.

Endpoints:
    GET  /api/llm/settings  → list providers with current config
    POST /api/llm/settings  → set provider key/model/enabled
    POST /api/llm/settings/{provider}/probe → live-test connectivity
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

router = APIRouter()

# Recommended default models — one per provider that handles all tasks
# (chat, extraction, vision) with a single key.
RECOMMENDED_MODELS: dict[str, str] = {
    "dashscope": "qwen-max",
    "openai": "gpt-4o",
    "anthropic": "claude-sonnet-4-20250514",
    "google": "gemini-2.5-flash",
    "deepseek": "deepseek-chat",
    "openrouter": "openai/gpt-4o",
}


class LlmSettingsPayload(BaseModel):
    provider: str
    apiKey: str | None = None       # omit to leave unchanged
    enabled: bool | None = None
    defaultModel: str | None = None
    clearKey: bool = False           # set true to remove the stored key


@router.get("/llm/settings")
def get_llm_settings(db: Session = Depends(get_session)) -> dict:
    """Return every known provider with its current state + recommended model."""
    from app import llm_admin

    providers = llm_admin.list_providers(db)
    for p in providers:
        p["recommendedModel"] = RECOMMENDED_MODELS.get(p["provider"])
    return {"providers": providers}


@router.post("/llm/settings")
def set_llm_settings(
    payload: LlmSettingsPayload,
    db: Session = Depends(get_session),
) -> dict:
    """Set API key, model, or enabled state for a provider."""
    from app import llm_admin

    provider = payload.provider
    if provider not in llm_admin.PROVIDER_KEY_ATTR:
        # Check custom providers
        if llm_admin.get_custom_provider(db, provider) is not None:
            try:
                result = llm_admin.set_custom_provider(
                    db, provider,
                    api_key=payload.apiKey,
                    clear_key=payload.clearKey,
                    enabled=payload.enabled,
                    default_model=payload.defaultModel,
                )
                result["recommendedModel"] = None
                return result
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e))
        raise HTTPException(status_code=404, detail=f"unknown provider: {provider!r}")

    try:
        result = llm_admin.set_provider(
            db, provider,
            enabled=payload.enabled,
            api_key=payload.apiKey,
            default_model=payload.defaultModel,
            clear_key=payload.clearKey,
        )
        result["recommendedModel"] = RECOMMENDED_MODELS.get(provider)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/llm/settings/{provider}/probe")
def probe_llm_provider(
    provider: str,
    db: Session = Depends(get_session),
) -> dict:
    """Live-test a provider's connectivity + key validity."""
    from app import llm_admin

    if provider not in llm_admin.PROVIDER_KEY_ATTR:
        if llm_admin.get_custom_provider(db, provider) is not None:
            return llm_admin.probe_custom(db, provider)
        raise HTTPException(status_code=404, detail=f"unknown provider: {provider!r}")

    return llm_admin.probe(db, provider)
