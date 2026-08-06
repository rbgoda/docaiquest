"""LLM gateway — provider-agnostic `call()` interface.

One call site for the whole platform. The model-id prefix maps to a backend:

  | Prefix                   | Backend         | Notes                          |
  |--------------------------|-----------------|--------------------------------|
  | `openrouter/...`         | OpenRouter      | unified — Qwen/DeepSeek/Gemini/Llama/Anthropic via one API key, supports `:free` model variants |
  | `anthropic/...`          | Anthropic       | direct — needed for prompt caching |
  | `google/...`             | Google GenAI    | direct — free-tier Gemini Flash |
  | anything else            | stub            | canned response for dev / CI   |

Backends return a common `CompletionResult` shape so the tier router doesn't
care which provider answered. Token usage + latency live here; cost lookup
happens one layer up (router/ledger).

We deliberately don't use the official SDKs — `httpx` against each provider's
HTTP API keeps the dependency footprint tiny and the cold-start fast. The
OpenAI-compatible API surface is identical across OpenRouter / DeepSeek /
many local servers, which is most of the point.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Literal

import httpx

from app.config import get_settings

log = logging.getLogger("docaiq.llm.gateway")


# ---- Public shape ---------------------------------------------------------
Role = Literal["system", "user", "assistant", "tool"]


@dataclass(frozen=True)
class Message:
    role: Role
    # str for text-only; list of OpenAI-style content blocks for multi-modal.
    # Each block is {"type": "text", "text": ...} or
    # {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,..."}}.
    # The dataclass stays frozen so prompts can be hashed for caching.
    content: str | list[dict]
    # Native tool-use: assistant messages carry tool_calls, tool messages carry
    # tool_call_id. None for system/user messages.
    tool_calls: list[dict] | None = None
    tool_call_id: str | None = None


@dataclass
class CompletionResult:
    text: str
    model: str                      # the model the provider actually used
    provider: str                   # "openrouter" | "anthropic" | "google" | "stub"
    input_tokens: int
    output_tokens: int
    latency_ms: int
    raw_json: dict | None = None    # full JSON response, useful for cost calc + debug
    structured: dict | None = None  # parsed structured-output (if requested + valid)
    finish_reason: str | None = None
    # Tool-use replies — list of {function: {name, arguments}, id?}. None when
    # tools= wasn't passed or the model chose to answer in text.
    tool_calls: list[dict] | None = None


# ---- Entry point ----------------------------------------------------------

def _read_pii_config() -> dict:
    """Read the effective PII config from the in-memory cache populated by
    the superadmin router. Returns defaults when the cache is cold (boot time,
    no DB access yet)."""
    try:
        from app.routers.superadmin import get_cached_pii_config
        return get_cached_pii_config()
    except Exception:
        return {}


def call(
    model: str,
    messages: list[Message],
    *,
    temperature: float = 0.2,
    max_tokens: int = 512,
    structured: bool = False,
    tools: list[dict] | None = None,
    tool_choice: dict | str | None = None,
    cache_system: bool = False,
    # M44.P11 · privacy / audit context. Callers in the chat pipeline
    # know the tenant + user + doc + task; passing them through here
    # lets us write a useful audit row without depending on a
    # contextvar that may not be set in all code paths.
    tenant_id: str | None = None,
    user_email: str | None = None,
    doc_id_external: str | None = None,
    task_kind: str | None = None,
    extra_terms: list[tuple[str, str]] | None = None,
) -> CompletionResult:
    """Dispatch to the right backend. Synchronous — Arq worker + FastAPI thread
    pool give us concurrency, and SDK-free httpx avoids async/sync split-brain.

    `cache_system=True` flags the system prompt for prompt caching when
    the provider supports it (Anthropic native + Anthropic via OpenRouter
    when the model accepts `cache_control` blocks). 90% discount on the
    cached prefix tokens for 5 minutes.

    M44.P11 · privacy:
      · PII redaction is controlled by the admin UI (stored in routing_config.pii).
        When enabled AND at least one category group is active AND `tenant_id`
        is provided, every USER message is run through `app.pii.redact()` with
        only the active categories before being sent. The system prompt is
        augmented with an instruction to preserve placeholders. The response
        is detokenized back to original values.
      · If `llm_provider_allowlist` is set and the resolved provider
        isn't on it, a `LLMProviderBlockedError` is raised.
      · Every call writes an audit row (when `llm_audit_enabled`) with
        SHA-256 of the FINAL post-redaction prompt and the response.
    """
    settings = get_settings()
    backend, model_id = _resolve_backend(model)

    # M53 · LLM spend guard (budget kill-switch + per-user hourly cap). Raises
    # CostCapExceeded when a ceiling is hit; callers degrade gracefully. No-op
    # when caps are 0 (default) or Redis is down.
    try:
        from app.cost_guard import guard as _cost_guard
        from app.documents_scope import get_current_owner_user_pk
        _cost_guard(tenant_id, get_current_owner_user_pk())
    except ImportError:
        pass

    # M44.P11 · provider allowlist enforcement BEFORE we do any work.
    allowlist = [p.strip() for p in (settings.llm_provider_allowlist or "").split(",") if p.strip()]
    if allowlist and backend not in allowlist:
        raise LLMProviderBlockedError(
            f"tenant policy blocks provider '{backend}' · allowed: {allowlist}"
        )

    # M44.P11 · PII redaction on user messages. System messages are not
    # redacted because they're our prompts (no PII inside them).
    pii_mapping: dict[str, str] = {}
    pii_counts: dict[str, int] = {}
    # Read PII config from DB cache → build the set of active category groups.
    # Only redacts if the admin has enabled PII + at least one category is on.
    _pii_cfg = _read_pii_config()
    _pii_enabled = _pii_cfg.get("enabled", False) if isinstance(_pii_cfg, dict) else False
    _pii_categories: set[str] = set()
    if _pii_enabled and tenant_id:
        _cats = _pii_cfg.get("categories", {}) if isinstance(_pii_cfg, dict) else {}
        _pii_categories = {k for k, v in _cats.items() if v}
    if _pii_categories and tenant_id:
        from app.pii import PRESERVE_PLACEHOLDERS_INSTRUCTION, redact
        _redact_names = ("names" in _pii_categories)
        # Use a content-based seed so the same text always gets the same placeholders.
        # This is critical for Anthropic prompt caching — the cache_prefix must have
        # identical tokens across turns for the 90% cache discount to materialize.
        _base_seed = 1
        redacted_messages: list[Message] = []
        for m in messages:
            if m.role == "user" and isinstance(m.content, str):
                r = redact(m.content, extra_terms=extra_terms, placeholder_seed=_base_seed, redact_names=_redact_names, mask_categories=_pii_categories)
                pii_mapping.update(r.mapping)
                for k, v in r.counts.items():
                    pii_counts[k] = pii_counts.get(k, 0) + v
                _base_seed += len(r.mapping)
                redacted_messages.append(Message(role="user", content=r.text))
            elif m.role == "user" and isinstance(m.content, list):
                # M50 · multi-part user content (e.g. a cache_control doc block) —
                # redact each text part in place, preserving cache_control + type.
                new_parts: list[dict] = []
                for part in m.content:
                    if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str):
                        r = redact(part["text"], extra_terms=extra_terms, placeholder_seed=_base_seed, redact_names=_redact_names, mask_categories=_pii_categories)
                        pii_mapping.update(r.mapping)
                        for k, v in r.counts.items():
                            pii_counts[k] = pii_counts.get(k, 0) + v
                        _base_seed += len(r.mapping)
                        np = dict(part)
                        np["text"] = r.text
                        new_parts.append(np)
                    else:
                        new_parts.append(part)
                redacted_messages.append(Message(role="user", content=new_parts))
            elif m.role == "system" and isinstance(m.content, str):
                # Augment system prompt with placeholder-preservation rule.
                redacted_messages.append(Message(
                    role="system",
                    content=m.content + "\n\n" + PRESERVE_PLACEHOLDERS_INSTRUCTION,
                ))
            else:
                redacted_messages.append(m)
        messages = redacted_messages

    t0 = time.perf_counter()
    http_status: int | None = None
    failure_kind: str | None = None
    try:
        if backend == "openrouter":
            result = _openrouter(model_id, messages, temperature, max_tokens, structured, tools, tool_choice, cache_system)
        elif backend == "anthropic":
            result = _anthropic(model_id, messages, temperature, max_tokens, structured, cache_system)
        elif backend == "google":
            result = _google(model_id, messages, temperature, max_tokens, structured, tools, tool_choice)
        elif backend == "dashscope":
            result = _dashscope(model_id, messages, temperature, max_tokens, structured, tools, tool_choice)
        elif backend == "ollama":
            result = _ollama(model_id, messages, temperature, max_tokens, structured, tools, tool_choice)
        elif backend == "deepseek":
            result = _openai_compat(
                model_id, messages, temperature, max_tokens,
                structured, tools, tool_choice,
                base_url="https://api.deepseek.com/v1",
                api_key=settings.deepseek_api_key or "",
                provider="deepseek",
            )
        elif backend == "proxy" or model.startswith("proxy/"):
            result = _proxy_stub(model_id, messages)
        elif tenant_id and backend in _CUSTOM_CACHE.get(tenant_id, {}):
            cfg = _CUSTOM_CACHE[tenant_id][backend]
            result = _openai_compat(
                model_id, messages, temperature, max_tokens,
                structured, tools, tool_choice,
                base_url=cfg["base_url"], api_key=cfg.get("api_key", ""),
                provider=backend,
            )
        else:
            result = _stub(model_id, messages, structured)
        http_status = 200
    except httpx.HTTPStatusError as e:
        http_status = e.response.status_code if e.response is not None else None
        failure_kind = type(e).__name__
        raise
    except Exception as e:  # noqa: BLE001
        failure_kind = type(e).__name__
        raise
    finally:
        elapsed_ms = int((time.perf_counter() - t0) * 1000)

    result.latency_ms = elapsed_ms

    # M44.P11 · detokenize response so callers see real values
    if pii_mapping and result.text:
        import re as _re

        from app.pii import detokenize
        result.text = detokenize(result.text, pii_mapping)
        # M51 · strip any "(redacted)/(masked)/(hidden)" the model annotated next
        # to a now-revealed value — the value IS shown, so the label misleads.
        result.text = _re.sub(r"\s*\((?:redacted|masked|hidden|pii(?:[ -]?redacted)?)\)",
                              "", result.text, flags=_re.IGNORECASE)

    # Parse structured output from the DETOKENIZED text — so structured-mode callers
    # (e.g. #4 typed_answer reading result.structured) get real values, not [PERSON_1]
    # placeholders. Re-derive even if a backend pre-populated it from tokenized JSON.
    if structured and result.text:
        parsed = _try_parse_json(result.text)
        if parsed is not None:
            result.structured = parsed

    # M44.P11 · audit ledger · hashes only, never content
    if settings.llm_audit_enabled and tenant_id:
        try:
            from app.llm_audit import record_call
            from app.pii import fingerprint
            prompt_concat = "".join(
                m.content if isinstance(m.content, str) else "" for m in messages
            )
            record_call(
                tenant_id=tenant_id,
                user_email=user_email,
                provider=backend,
                model=model_id,
                task_kind=task_kind,
                doc_id_external=doc_id_external,
                prompt_sha256=fingerprint(prompt_concat),
                response_sha256=fingerprint(result.text) if result.text else None,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                pii_entities_redacted=sum(pii_counts.values()),
                pii_kinds=pii_counts or None,
                latency_ms=elapsed_ms,
                http_status=http_status,
                failure_kind=failure_kind,
            )
        except Exception:  # noqa: BLE001
            pass  # audit must never break the caller

    return result


class LLMProviderBlockedError(RuntimeError):
    """Raised when the resolved provider isn't on the tenant's allowlist.
    Caller should retry with a different model or surface a clean 403
    to the operator."""


class CloudProxyUnavailableError(RuntimeError):
    """Raised when a model/task needs the DocAIQ Cloud proxy but it isn't
    configured (license_mode != cloud, or proxy keys missing). Phase 4
    replaces the placeholder with a real proxy call; callers may treat
    this as a hard 'cloud required' signal."""


# ---- Backend selection ----------------------------------------------------
# Per-provider prefix → (backend name, settings attribute for the API key).
# Any model that starts with one of these prefixes AND has the corresponding
# key set routes to that provider. A model with a recognised prefix but a
# MISSING key is a likely misconfiguration — we warn in that case because the
# call will silently degrade to the stub (0.83 confidence, auto-approve band).
_PREFIX_MAP: tuple[tuple[str, str, str], ...] = (
    ("openrouter/", "openrouter", "openrouter_api_key"),
    ("anthropic/",  "anthropic",  "anthropic_api_key"),
    ("google/",     "google",     "google_genai_api_key"),
    ("dashscope/",  "dashscope",  "dashscope_api_key"),
    ("deepseek/",   "deepseek",   "deepseek_api_key"),
    ("ollama/",     "ollama",     "ollama_api_key"),
)


# ═══════════════════════════════════════════════════════════════════════════════
# Custom (OpenAI-compatible) provider cache
# ═══════════════════════════════════════════════════════════════════════════════
# tenant_id → {slug: {label, base_url, api_key}}.  Populated eagerly at boot
# + after admin mutations (via llm_admin._refresh_gateway_cache) and lazily
# revived every _CUSTOM_CACHE_TTL as a multi-worker safety net.  Decrypted keys
# live in memory — same exposure class as the settings singleton.
_CUSTOM_CACHE: dict[str, dict[str, dict]] = {}
_CUSTOM_LOADED_AT: dict[str, float] = {}
_CUSTOM_CACHE_TTL = 30.0  # seconds between lazy DB refreshes


def _tenant_id_from_context() -> str | None:
    try:
        from app.db import get_current_tenant
        return get_current_tenant()
    except Exception:  # noqa: BLE001 — boot / CI / tests without tenant context
        return None


def _ensure_custom_loaded(tenant_id: str | None) -> None:
    if not tenant_id:
        return
    now = time.monotonic()
    if tenant_id in _CUSTOM_CACHE and (
        now - _CUSTOM_LOADED_AT.get(tenant_id, 0) < _CUSTOM_CACHE_TTL
    ):
        return
    try:
        from app.db import SessionLocal
        from app.llm_admin import effective_custom_providers  # noqa: PLC0415
        with SessionLocal() as s:
            _CUSTOM_CACHE[tenant_id] = effective_custom_providers(s)
        _CUSTOM_LOADED_AT[tenant_id] = time.monotonic()
    except Exception:  # noqa: BLE001 — broken cache must never break an LLM call
        _CUSTOM_CACHE.setdefault(tenant_id, {})


def _set_custom_cache(entries: dict[str, dict], tenant_id: str | None = None) -> None:
    """Explicit cache push from llm_admin after mutations and at boot."""
    tid = tenant_id or _tenant_id_from_context()
    if tid:
        _CUSTOM_CACHE[tid] = dict(entries)
        _CUSTOM_LOADED_AT[tid] = time.monotonic()


def _resolve_backend(model: str) -> tuple[str, str]:
    """Map a model id to (backend_name, model_id_for_provider).

    ``openrouter/...`` strips the prefix because OpenRouter takes the rest
    directly (e.g. ``google/gemini-2.0-flash-exp:free``). For native paths the
    prefix is the provider name and we strip it too.
    """
    settings = get_settings()

    # P2 · DocAIQ Cloud proxy. Special-cased BEFORE the key-gated providers:
    # a proxy/ model must never silently fall through to the canned stub —
    # the placeholder raises CloudProxyUnavailableError until Phase 4 wires
    # the real proxy client.
    if model.startswith("proxy/"):
        return "proxy", model.removeprefix("proxy/")

    for prefix, backend, key_attr in _PREFIX_MAP:
        if model.startswith(prefix):
            if getattr(settings, key_attr, None):
                return backend, model.removeprefix(prefix)
            # Ollama is valid without an API key — it just needs the base URL.
            if backend == "ollama" and getattr(settings, "ollama_base_url", None):
                return backend, model.removeprefix(prefix)
            # Model has a recognised provider prefix but the API key is
            # missing → likely a misconfiguration. Warn so operators can
            # catch it before the stub silently serves canned answers.
            log.warning(
                "Model %r routes to %r but %s is not set — "
                "falling through to stub (canned answers, 0.83 confidence). "
                "Set the corresponding API key or use a different model.",
                model, backend, key_attr.upper(),
            )
            return "stub", model
    # Custom providers (OpenAI-compatible, registered by superadmin at runtime).
    tid = _tenant_id_from_context()
    if tid:
        _ensure_custom_loaded(tid)
        for slug, cfg in _CUSTOM_CACHE.get(tid, {}).items():
            if model.startswith(slug + "/"):
                if cfg.get("base_url"):
                    return slug, model.removeprefix(slug + "/")
                log.warning(
                    "Model %r routes to custom %r but base_url is missing — "
                    "falling through to stub.", model, slug,
                )
                return "stub", model
    # No recognised prefix at all — legacy row or dev/CI. Stub is expected here.
    return "stub", model


# ---- OpenAI-compatible (OpenRouter) ---------------------------------------
def _openrouter(
    model: str, messages: list[Message], temperature: float, max_tokens: int,
    structured: bool, tools: list[dict] | None = None,
    tool_choice: dict | str | None = None,
    cache_system: bool = False,
) -> CompletionResult:
    settings = get_settings()
    # M44.P3.B · prompt caching · only wraps the system message and only
    # for Anthropic-hosted models on OpenRouter, which honor
    # `cache_control` blocks. For other models on OpenRouter the
    # cache_control marker is silently ignored — no harm, no help.
    out_messages = []
    is_anthropic_model = "anthropic/" in model or "claude" in model.lower()
    for m in messages:
        if cache_system and m.role == "system" and is_anthropic_model and isinstance(m.content, str):
            out_messages.append({
                "role": "system",
                "content": [{
                    "type": "text", "text": m.content,
                    "cache_control": {"type": "ephemeral"},
                }],
            })
        else:
            msg: dict[str, Any] = {"role": m.role, "content": m.content}
            if m.tool_calls is not None:
                msg["tool_calls"] = m.tool_calls
            if m.tool_call_id is not None:
                msg["tool_call_id"] = m.tool_call_id
            out_messages.append(msg)
    payload: dict[str, Any] = {
        "model": model,
        "messages": out_messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if tools:
        payload["tools"] = tools
        if tool_choice is not None:
            payload["tool_choice"] = tool_choice
    # `response_format: json_object` is unreliable on OpenRouter's free
    # models — gpt-oss/gemma/qwen-free either ignore it or emit malformed
    # JSON. Skipped here; the Validator agent parses natural-language
    # replies with a `Confidence: 0.XX` marker. Paid providers (Anthropic,
    # Google) get strict JSON via their native paths below.
    _ = structured  # intentionally unused; signature kept for caller parity

    headers = {
        "Authorization": f"Bearer {settings.openrouter_api_key}",
        "Content-Type": "application/json",
        # OpenRouter requires these for free-tier abuse tracking.
        "HTTP-Referer": settings.openrouter_referer,
        "X-Title": settings.openrouter_app_title,
    }
    # TODO #38 — bounded retry with Retry-After honoring. OpenRouter
    # returns 429 frequently on free-tier models; the cascade was treating
    # any 429 as a tier-fail and demoting through the chain, often hitting
    # the same per-minute ceiling on every model in sequence. With this
    # wrapper, transient 429/5xx get one retry waiting up to the
    # provider's `Retry-After` header (capped at ~3s to bound latency).
    body = _post_with_retry(
        "https://openrouter.ai/api/v1/chat/completions",
        payload=payload,
        headers=headers,
        timeout=settings.llm_request_timeout,
    )
    choice = body["choices"][0]
    # NOTE: we do NOT fall back to `message.reasoning` even when `content` is
    # empty. Reasoning is the model's *internal* scratchpad — exposing it as
    # the answer leaks chain-of-thought and confuses the user. If the answer
    # tier emits empty content, that tier failed for our purposes and the
    # router should escalate.
    text = (choice["message"].get("content") or "").strip()
    tool_calls = choice["message"].get("tool_calls") or None
    usage = body.get("usage") or {}
    return CompletionResult(
        text=text,
        model=body.get("model") or model,
        provider="openrouter",
        input_tokens=int(usage.get("prompt_tokens", 0)),
        output_tokens=int(usage.get("completion_tokens", 0)),
        latency_ms=0,
        raw_json=body,
        finish_reason=choice.get("finish_reason"),
        tool_calls=tool_calls,
    )


# ---- Anthropic direct -----------------------------------------------------
def _anthropic(
    model: str, messages: list[Message], temperature: float, max_tokens: int,
    structured: bool, cache_system: bool = False,
) -> CompletionResult:
    settings = get_settings()
    # Anthropic's chat API takes the system prompt as a top-level field, not
    # in the message list. Split it out.
    system_text = "\n\n".join(
        m.content for m in messages if m.role == "system" and isinstance(m.content, str)
    )
    others = []
    for m in messages:
        if m.role == "system":
            continue
        item: dict[str, Any] = {"role": m.role if m.role in ("user", "assistant", "tool") else "assistant",
                                "content": m.content}
        if m.tool_calls is not None:
            item["tool_calls"] = m.tool_calls
        if m.tool_call_id is not None:
            item["tool_call_id"] = m.tool_call_id
        others.append(item)
    if structured:
        system_text = (system_text + "\n\nReply with valid JSON only, no prose.").strip()

    # M44.P3.B · prompt caching · cached prefix gets a 90% discount for
    # 5 minutes. The system block becomes a content-array with
    # cache_control. Only do this for substantial prompts — Anthropic
    # has a minimum of 1024 tokens for the cache to take effect.
    if cache_system and len(system_text) >= 4000:  # ~1K tokens
        system_param: Any = [{
            "type": "text", "text": system_text,
            "cache_control": {"type": "ephemeral"},
        }]
    else:
        system_param = system_text

    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "system": system_param,
        "messages": others,
    }
    headers = {
        "x-api-key": settings.anthropic_api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    response = httpx.post(
        "https://api.anthropic.com/v1/messages",
        json=payload, headers=headers,
        timeout=settings.llm_request_timeout,
    )
    response.raise_for_status()
    body = response.json()
    text = "".join(c.get("text", "") for c in body.get("content", []) if c.get("type") == "text")
    usage = body.get("usage") or {}
    return CompletionResult(
        text=text,
        model=body.get("model") or model,
        provider="anthropic",
        input_tokens=int(usage.get("input_tokens", 0)),
        output_tokens=int(usage.get("output_tokens", 0)),
        latency_ms=0,
        raw_json=body,
        finish_reason=body.get("stop_reason"),
    )


# ---- Google GenAI direct --------------------------------------------------
def _google(
    model: str, messages: list[Message], temperature: float, max_tokens: int,
    structured: bool, tools: list[dict] | None = None,
    tool_choice: dict | str | None = None,
) -> CompletionResult:
    settings = get_settings()
    # Google's REST endpoint uses ?key=... auth. Map our roles into theirs.
    contents = []
    system_text = ""
    for m in messages:
        if m.role == "system":
            system_text += ("\n" if system_text else "") + (m.content if isinstance(m.content, str) else "")
            continue

        role = "user" if m.role == "user" else "model"
        parts: list[dict[str, Any]] = []
        content = m.content

        if isinstance(content, list):
            # Multimodal: OpenAI-style blocks → Gemini parts
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        parts.append({"text": block.get("text", "")})
                    elif block.get("type") == "image_url":
                        url = block.get("image_url", {}).get("url", "")
                        # data:image/jpeg;base64,<data>
                        if url.startswith("data:"):
                            header, b64 = url.split(",", 1) if "," in url else ("", url)
                            mime = header.split(":")[1].split(";")[0] if ":" in header else "image/jpeg"
                            parts.append({"inlineData": {"mimeType": mime, "data": b64}})
                        else:
                            # External URL — Gemini needs fileData for this
                            parts.append({"fileData": {"mimeType": "image/jpeg", "fileUri": url}})
                elif isinstance(block, str):
                    parts.append({"text": block})
        elif isinstance(content, str):
            parts.append({"text": content})
        else:
            parts.append({"text": str(content)})

        contents.append({"role": role, "parts": parts})

    gen_config: dict[str, Any] = {"temperature": temperature, "maxOutputTokens": max_tokens}
    payload: dict[str, Any] = {"contents": contents, "generationConfig": gen_config}

    if structured:
        gen_config["responseMimeType"] = "application/json"
        # M31.8 · Enforce a strict schema so every JSON reply has the same
        # shape — {answer, confidence, satisfied, citations}.
        gen_config["responseSchema"] = {
            "type": "object",
            "properties": {
                "answer": {"type": "string", "description": "1-3 sentence verdict citing chunk-N markers."},
                "confidence": {"type": "number", "description": "P(document satisfies requirement), 0.0-1.0."},
                "satisfied": {"type": "boolean", "description": "True if requirement is met."},
                "citations": {"type": "array", "items": {"type": "string"}, "description": "List of chunk-N ids used."},
            },
            "required": ["answer", "confidence", "satisfied"],
            "propertyOrdering": ["answer", "confidence", "satisfied", "citations"],
        }

    # --- Native function calling (tools → Gemini format) ---
    if tools:

        def _clean_schema_for_gemini(node: dict) -> dict:
            """Recursively convert an OpenAI JSON Schema node to Gemini-compatible format.

            Gemini's Schema type differs from standard JSON Schema:
            - No \"date\" / \"date-time\" type — map to \"string\"
            - No per-property \"required\" boolean — only object-level required array
            - No \"example\" / \"default\" / \"const\" at property level
            - INTEGER is distinct from NUMBER (keep if explicit)
            """
            if not isinstance(node, dict):
                # Some DB library schemas have bare strings as property values
                # (e.g. "street_address_2": "string" instead of {"type": "string"}).
                # Wrap them so Gemini sees a valid Schema.
                if isinstance(node, str):
                    return {"type": "string"}
                return node
            out: dict[str, Any] = {}
            # type mapping
            raw_type = node.get("type", "string")
            if raw_type in ("date", "date-time", "time", "datetime"):
                out["type"] = "string"
            elif raw_type == "integer":
                out["type"] = "integer"
            elif raw_type == "number":
                out["type"] = "number"
            elif raw_type == "boolean":
                out["type"] = "boolean"
            elif raw_type in ("array", "object"):
                out["type"] = raw_type
            else:
                out["type"] = "string"
            # description
            if "description" in node:
                out["description"] = node["description"]
            # enum
            if "enum" in node:
                out["enum"] = node["enum"]
            # properties (OBJECT)
            if "properties" in node and isinstance(node["properties"], dict):
                out["properties"] = {
                    k: _clean_schema_for_gemini(v)
                    for k, v in node["properties"].items()
                }
            # required — ONLY at object level, only if it's a list
            if out.get("type") == "object" and "required" in node:
                req = node["required"]
                if isinstance(req, list):
                    out["required"] = [str(r) for r in req]
            # items (ARRAY)
            # items (ARRAY) — Gemini REQUIRES items on every array
            if out.get("type") == "array":
                items = node.get("items")
                if isinstance(items, dict):
                    out["items"] = _clean_schema_for_gemini(items)
                elif isinstance(items, str) and items == "properties":
                    # Schema Library shorthand: items="properties" means
                    # "array of objects" — the actual properties are on the
                    # parent field, not here. Default to an object with no
                    # known shape so the LLM infers it.
                    out["items"] = {"type": "object", "properties": {}}
                else:
                    # No items, or unrecognised string — default to strings
                    out["items"] = {"type": "string"}

            # Remove per-property `required` boolean — Gemini only supports
            # array-level required. The library schema stores this for its
            # own form rendering; it's noise for the LLM.
            for drop_key in ("required", "unique", "default", "enumSource"):
                out.pop(drop_key, None)

            return out

        gemini_tools: list[dict[str, Any]] = []
        for t in tools:
            fn = t.get("function", {})
            params = fn.get("parameters", {})
            cleaned = _clean_schema_for_gemini(params) if params else {"type": "object", "properties": {}}
            gemini_tools.append({
                "functionDeclarations": [{
                    "name": fn.get("name", ""),
                    "description": fn.get("description", ""),
                    "parameters": cleaned,
                }]
            })
        payload["tools"] = gemini_tools

        # tool_choice → Gemini toolConfig
        mode: str = "AUTO"
        allowed_names: list[str] | None = None
        if tool_choice == "none":
            mode = "NONE"
        elif tool_choice == "auto":
            mode = "AUTO"
        elif isinstance(tool_choice, dict):
            fn_name = tool_choice.get("function", {}).get("name", "")
            if fn_name:
                mode = "ANY"
                allowed_names = [fn_name]
        elif isinstance(tool_choice, str) and tool_choice:
            mode = "ANY"
            allowed_names = [tool_choice]

        payload["toolConfig"] = {
            "functionCallingConfig": {"mode": mode}
        }
        if allowed_names:
            payload["toolConfig"]["functionCallingConfig"]["allowedFunctionNames"] = allowed_names

    if system_text:
        payload["systemInstruction"] = {"parts": [{"text": system_text}]}

    # §A7 · pass the key in the x-goog-api-key HEADER, not the URL query, so it
    # never appears in a logged request URL.
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent"
    )
    response = httpx.post(url, json=payload, timeout=settings.llm_request_timeout,
                          headers={"x-goog-api-key": settings.google_genai_api_key})
    if response.status_code >= 400:
        log.warning("google: HTTP %s · body=%s", response.status_code,
                     (response.text or "")[:2000])
        response.raise_for_status()
    body = response.json()
    cand = (body.get("candidates") or [{}])[0]
    content = cand.get("content") or {}
    parts_list = content.get("parts", [])

    # Collect text from text parts
    text = "".join(p.get("text", "") for p in parts_list if "text" in p)

    # Parse Gemini functionCall → OpenAI-compatible tool_calls
    tool_calls = None
    for p in parts_list:
        fc = p.get("functionCall")
        if fc:
            tool_calls = [{
                "id": fc.get("name", "call_0"),
                "type": "function",
                "function": {
                    "name": fc.get("name", ""),
                    "arguments": json.dumps(fc.get("args", {}), ensure_ascii=False)
                    if isinstance(fc.get("args"), dict) else str(fc.get("args", "")),
                },
            }]
            break  # only one function call per turn

    meta = body.get("usageMetadata") or {}
    return CompletionResult(
        text=text,
        model=model,
        provider="google",
        input_tokens=int(meta.get("promptTokenCount", 0)),
        output_tokens=int(meta.get("candidatesTokenCount", 0)),
        latency_ms=0,
        raw_json=body,
        finish_reason=cand.get("finishReason"),
        tool_calls=tool_calls,
    )


# ---- Alibaba Dashscope (Qwen3-VL direct) ----------------------------------
def _dashscope(
    model: str, messages: list[Message], temperature: float, max_tokens: int,
    structured: bool, tools: list[dict] | None = None,
    tool_choice: dict | str | None = None,
) -> CompletionResult:
    """Direct call to Alibaba's Dashscope (OpenAI-compatible API).

    Why this exists separately from OpenRouter: Dashscope hosts the
    proprietary Qwen models (qwen3-vl-235b-a22b, qwen-vl-max, qwen-plus,
    qwen-turbo) on their own infrastructure with separate rate limits
    from OpenRouter's shared free tier. The xpenseaiq-v5 project uses
    this path; we benchmark against their answer quality.

    The wire protocol is identical to OpenRouter / OpenAI · just a
    different endpoint + auth header.
    """
    settings = get_settings()
    dash_msgs: list[dict[str, Any]] = []
    for m in messages:
        dm: dict[str, Any] = {"role": m.role, "content": m.content}
        if m.tool_calls is not None:
            dm["tool_calls"] = m.tool_calls
        if m.tool_call_id is not None:
            dm["tool_call_id"] = m.tool_call_id
        dash_msgs.append(dm)
    payload: dict[str, Any] = {
        "model": model,
        "messages": dash_msgs,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if tools:
        payload["tools"] = tools
        if tool_choice is not None:
            payload["tool_choice"] = tool_choice
    if structured:
        # Dashscope honors `response_format: json_object` on qwen-max and
        # qwen3-vl-235b. Cheaper Qwen variants ignore it · the parser
        # in _try_parse_json catches non-JSON output downstream.
        payload["response_format"] = {"type": "json_object"}

    headers = {
        "Authorization": f"Bearer {settings.dashscope_api_key}",
        "Content-Type": "application/json",
    }
    base = settings.dashscope_base_url.rstrip("/")
    body = _post_with_retry(
        f"{base}/chat/completions",
        payload=payload, headers=headers,
        timeout=settings.llm_request_timeout,
    )
    choice = body["choices"][0]
    text = (choice["message"].get("content") or "").strip()
    tool_calls = choice["message"].get("tool_calls") or None
    usage = body.get("usage") or {}
    return CompletionResult(
        text=text,
        model=body.get("model") or model,
        provider="dashscope",
        input_tokens=int(usage.get("prompt_tokens", 0)),
        output_tokens=int(usage.get("completion_tokens", 0)),
        latency_ms=0,
        raw_json=body,
        finish_reason=choice.get("finish_reason"),
        tool_calls=tool_calls,
    )


# ---- Ollama (OpenAI-compatible local) ------------------------------------
def _ollama(
    model: str, messages: list[Message], temperature: float, max_tokens: int,
    structured: bool, tools: list[dict] | None = None,
    tool_choice: dict | str | None = None,
) -> CompletionResult:
    settings = get_settings()
    url = f"{settings.ollama_base_url.rstrip('/')}/api/chat"
    payload: dict[str, Any] = {
        "model": model,
        "messages": [{"role": m.role, "content": m.content} for m in messages],
        "stream": False,
        "options": {"temperature": temperature, "num_predict": max_tokens},
    }
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if settings.ollama_api_key:
        headers["Authorization"] = f"Bearer {settings.ollama_api_key}"
    data = _post_with_retry(url, payload=payload, headers=headers, timeout=120.0)
    # Ollama response: {"model":"llama3","message":{"role":"assistant","content":"..."},...}
    msg = data.get("message") or {}
    text = msg.get("content", "") or ""
    return CompletionResult(
        text=text,
        model=data.get("model", model),
        provider="ollama",
        input_tokens=data.get("prompt_eval_count"),
        output_tokens=data.get("eval_count"),
        latency_ms=0,
        raw_json=data,
        finish_reason="stop" if data.get("done") else None,
    )


# ---- OpenAI-compatible (generic) ------------------------------------------
def _openai_compat(
    model: str, messages: list[Message], temperature: float, max_tokens: int,
    structured: bool, tools: list[dict] | None = None,
    tool_choice: dict | str | None = None,
    *, base_url: str, api_key: str, provider: str,
) -> CompletionResult:
    """Generic OpenAI-compatible chat endpoint — Groq, Together, Fireworks,
    vLLM, LM Studio, and any other provider speaking the standard HTTP API.
    ``base_url`` INCLUDES the API version segment (…/openai/v1); the
    /chat/completions path is appended.  Request/response shape mirrors
    ``_dashscope`` / ``_openrouter``."""
    out: list[dict[str, Any]] = [
        {"role": m.role, "content": m.content} for m in messages
    ]
    payload: dict[str, Any] = {
        "model": model, "messages": out,
        "temperature": temperature, "max_tokens": max_tokens,
    }
    if tools:
        payload["tools"] = tools
        if tool_choice is not None:
            payload["tool_choice"] = tool_choice
    if structured:
        payload["response_format"] = {"type": "json_object"}
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    body = _post_with_retry(
        f"{base_url.rstrip('/')}/chat/completions",
        payload=payload, headers=headers,
        timeout=get_settings().llm_request_timeout,
    )
    choice = body["choices"][0]
    text = (choice["message"].get("content") or "").strip()
    tool_calls = choice["message"].get("tool_calls") or None
    usage = body.get("usage") or {}
    return CompletionResult(
        text=text,
        model=body.get("model") or model,
        provider=provider,
        input_tokens=int(usage.get("prompt_tokens", 0)),
        output_tokens=int(usage.get("completion_tokens", 0)),
        latency_ms=0,
        raw_json=body,
        finish_reason=choice.get("finish_reason"),
        tool_calls=tool_calls,
    )


# ---- Cloud proxy stub (Phase 2 placeholder, Phase 4 real client) ----------
def _proxy_stub(model: str, messages: list[Message]) -> CompletionResult:
    """P2 placeholder for the DocAIQ Cloud proxy client (Phase 4). Cloud-only
    tasks reach here when proxy_base_url/proxy_api_key are unset. Refuses
    loudly instead of silently degrading to canned answers."""
    raise CloudProxyUnavailableError(
        f"Model {model!r} requires the DocAIQ Cloud proxy, which is not configured "
        "on this deployment. Set DOCAIQ_LICENSE_MODE=cloud and configure "
        "DOCAIQ_PROXY_BASE_URL / DOCAIQ_PROXY_API_KEY, or use a local "
        "provider-prefixed model instead."
    )


# ---- Stub -----------------------------------------------------------------
def _stub(model: str, messages: list[Message], structured: bool) -> CompletionResult:
    """No-API-key fallback. Returns a deterministic canned answer so dev demos
    and CI both work without secrets. Confidence intentionally lands in the
    auto-approve band so the cascade doesn't escalate without keys.

    Every invocation logs a warning — if you see this on a deployed instance,
    a provider key is missing or a routing-config model id has no prefix."""
    log.warning(
        "LLM stub invoked for model=%r structured=%s — no real LLM call was made. "
        "Check that the model id has a provider prefix (openrouter/ anthropic/ google/ dashscope/) "
        "AND that the corresponding API key is set.",
        model, structured,
    )
    user = next((m.content for m in reversed(messages) if m.role == "user"), "")
    if structured:
        payload = {
            "answer": (
                f"(Stub LLM — no API key configured) For: \"{user[:80]}\". "
                "Based on retrieved evidence, the requirement appears satisfied. "
                "Configure DOCAIQ_OPENROUTER_API_KEY or DOCAIQ_ANTHROPIC_API_KEY "
                "for real reasoning."
            ),
            "confidence": 0.83,
            "citations": [],
            "bullets": [
                {"label": "1", "text": "Stub response — no LLM call was made.", "cite": None},
            ],
        }
        text = json.dumps(payload)
        return CompletionResult(
            text=text, model=model, provider="stub",
            input_tokens=len(user) // 4, output_tokens=len(text) // 4,
            latency_ms=0, structured=payload, finish_reason="stop",
        )
    text = f"(stub) {user[:120]}"
    return CompletionResult(
        text=text, model=model, provider="stub",
        input_tokens=len(user) // 4, output_tokens=len(text) // 4,
        latency_ms=0, finish_reason="stop",
    )


# ---- Helpers --------------------------------------------------------------
def _try_parse_json(text: str) -> dict | None:
    """Strip ```json fences if present, then attempt to parse. Returns None
    instead of raising — non-JSON output is a downgrade, not a hard failure."""
    s = text.strip()
    if s.startswith("```"):
        # Trim leading ```...``` fence
        first_nl = s.find("\n")
        if first_nl > 0:
            s = s[first_nl + 1 :]
        if s.endswith("```"):
            s = s[:-3]
    s = s.strip()
    try:
        return json.loads(s)
    except (ValueError, json.JSONDecodeError):
        return None


def _post_with_retry(url: str, *, payload: dict, headers: dict, timeout: float) -> dict:
    """One bounded retry on 429 / 5xx, honoring Retry-After (capped at 3s).
    Raises httpx.HTTPStatusError on permanent failure — caller decides
    whether the tier "failed" or to escalate."""
    import time as _time

    def _attempt() -> tuple[int, dict | None, float]:
        r = httpx.post(url, json=payload, headers=headers, timeout=timeout)
        wait = 0.0
        if r.status_code in (429, 503):
            try:
                ra = float(r.headers.get("Retry-After") or "0")
                wait = max(0.0, min(ra, 3.0))   # cap to 3 s
            except ValueError:
                wait = 1.0
        try:
            body = r.json() if r.content else None
        except Exception:  # noqa: BLE001
            body = None
        return r.status_code, body, wait

    # Provider hint for error messages · pulled from the URL so the same
    # _post_with_retry serves openrouter + dashscope + future providers
    # without misattributing failures.
    host = url.split("://", 1)[-1].split("/", 1)[0]
    provider_hint = host.split(".")[0] or "llm"

    status, body, wait = _attempt()
    if status >= 400 and status not in (429, 500, 502, 503, 504):
        # Permanent client errors — no retry. Include the provider's own
        # error message when it gave us one (e.g. 'model_not_found').
        body_msg = ""
        if isinstance(body, dict):
            err = body.get("error")
            if isinstance(err, dict):
                body_msg = " · " + str(err.get("message") or err.get("code") or "")[:200]
            elif isinstance(err, str):
                body_msg = " · " + err[:200]
        raise httpx.HTTPStatusError(
            f"{provider_hint} {status}{body_msg}",
            request=None, response=None,  # type: ignore[arg-type]
        )
    if status < 400:
        return body or {}
    # Retry once on rate-limit / transient
    if wait:
        _time.sleep(wait)
    status, body, _ = _attempt()
    if status < 400:
        return body or {}
    raise httpx.HTTPStatusError(
        f"{provider_hint} {status} (after retry)",
        request=None, response=None,  # type: ignore[arg-type]
    )


def available_backends() -> dict[str, bool]:
    """Report which backends are configured. Used by /api/llm/config so the
    frontend can disable model toggles for unset providers."""
    s = get_settings()
    out = {
        "openrouter": bool(s.openrouter_api_key),
        "anthropic": bool(s.anthropic_api_key),
        "google": bool(s.google_genai_api_key),
        "dashscope": bool(s.dashscope_api_key),
        "ollama": bool(s.ollama_base_url),
    }
    # Merge custom providers
    tid = _tenant_id_from_context()
    if tid:
        _ensure_custom_loaded(tid)
        for slug, cfg in _CUSTOM_CACHE.get(tid, {}).items():
            out[slug] = bool(cfg.get("base_url"))
    return out
