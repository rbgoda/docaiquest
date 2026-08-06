"""Superadmin LLM provider management — runtime key/enable/model config + probe.

Lets an operator configure LLM providers from the admin console (paste an API
key, enable/disable, set a default model) without a redeploy. The effective key
(DB override else env) is applied ONTO the settings singleton at boot + after
each save, so the gateway keeps reading `settings.<provider>_api_key` unchanged.
Keys are encrypted at rest (Fernet, key derived from jwt_secret).
"""
from __future__ import annotations

import base64
import hashlib
import logging
import re
import time

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_current_tenant
from app.orm import CustomLlmProvider, LlmProviderConfig

log = logging.getLogger("docaiq.llm_admin")

# provider → the Settings attribute the gateway reads for its API key.
PROVIDER_KEY_ATTR: dict[str, str] = {
    "openrouter": "openrouter_api_key",
    "anthropic": "anthropic_api_key",
    "google": "google_genai_api_key",
    "dashscope": "dashscope_api_key",
    "openai": "openai_api_key",
    "ollama": "ollama_api_key",
    "deepseek": "deepseek_api_key",
}
PROVIDERS = list(PROVIDER_KEY_ATTR)

# Custom provider slug validation — no '/' so model prefix routing is unambiguous.
_CUSTOM_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_RESERVED_SLUGS = set(PROVIDER_KEY_ATTR) | {"custom", "local", "stub"}


def _validate_custom_slug(slug: str) -> None:
    if not _CUSTOM_SLUG_RE.match(slug):
        raise ValueError(
            f"invalid slug {slug!r} — use lowercase letters, digits, '-' or '_'"
        )
    if slug in _RESERVED_SLUGS:
        raise ValueError(f"slug {slug!r} is a reserved provider name")

# Snapshot of the ORIGINAL env keys, captured once — so re-enabling a provider
# with no DB override restores the env value (we overwrite settings in place).
_ENV_KEYS: dict[str, str] | None = None


def _capture_env_keys() -> dict[str, str]:
    global _ENV_KEYS
    if _ENV_KEYS is None:
        s = get_settings()
        _ENV_KEYS = {p: (getattr(s, attr, "") or "") for p, attr in PROVIDER_KEY_ATTR.items()}
    return _ENV_KEYS


def _fernet():
    from cryptography.fernet import Fernet
    secret = (get_settings().jwt_secret or "docaiq-dev-insecure").encode("utf-8")
    key = base64.urlsafe_b64encode(hashlib.sha256(secret + b"docaiq-llm-key").digest())
    return Fernet(key)


def _encrypt(plain: str) -> str:
    return _fernet().encrypt(plain.encode("utf-8")).decode("ascii")


def _decrypt(enc: str) -> str:
    try:
        return _fernet().decrypt(enc.encode("ascii")).decode("utf-8")
    except Exception:  # noqa: BLE001
        return ""


def effective_key(db: Session, provider: str) -> str:
    """DB override (decrypted) if the provider row has one, else the env key.
    Returns "" when the provider is explicitly disabled."""
    env = _capture_env_keys().get(provider, "")
    row = db.get(LlmProviderConfig, (get_current_tenant(), provider))
    if row is None:
        return env
    if not row.enabled:
        return ""
    return _decrypt(row.api_key_enc) if row.api_key_enc else env


def apply_overrides(db: Session) -> None:
    """Apply DB provider config onto the settings singleton (boot + after save).
    Only providers WITH a config row are touched; others keep their env value."""
    _capture_env_keys()
    s = get_settings()
    try:
        rows = db.scalars(select(LlmProviderConfig).where(
            LlmProviderConfig.tenant_id == get_current_tenant())).all()
    except Exception:  # noqa: BLE001 — never break boot on this
        return
    for r in rows:
        attr = PROVIDER_KEY_ATTR.get(r.provider)
        if not attr:
            continue
        setattr(s, attr, effective_key(db, r.provider))
    if rows:
        log.info("llm_admin: applied %d provider override(s)", len(rows))


def list_providers(db: Session) -> list[dict]:
    env = _capture_env_keys()
    tid = get_current_tenant()
    rows = {r.provider: r for r in db.scalars(select(LlmProviderConfig).where(
        LlmProviderConfig.tenant_id == tid)).all()}
    out = []
    for p in PROVIDERS:
        r = rows.get(p)
        has_db = bool(r and r.api_key_enc)
        enabled = (r.enabled if r else True)
        source = "db" if has_db else ("env" if env.get(p) else "none")
        out.append({
            "provider": p,
            "enabled": enabled,
            "keySource": source,
            "configured": (
                enabled and (
                    bool(get_settings().ollama_base_url)
                    if p == "ollama"
                    else bool(effective_key(db, p))
                )
            ),
            "defaultModel": (r.default_model if r else None),
        })
    # Merge custom providers
    out.extend(list_custom_providers(db))
    return out


def set_provider(db: Session, provider: str, *, enabled: bool | None = None,
                 api_key: str | None = None, default_model: str | None = None,
                 clear_key: bool = False) -> dict:
    if provider not in PROVIDER_KEY_ATTR:
        raise ValueError(f"unknown provider {provider!r}")
    tid = get_current_tenant()
    row = db.get(LlmProviderConfig, (tid, provider))
    if row is None:
        row = LlmProviderConfig(tenant_id=tid, provider=provider, enabled=True)
        db.add(row)
    if enabled is not None:
        row.enabled = enabled
    if clear_key:
        row.api_key_enc = None
    elif api_key:
        row.api_key_enc = _encrypt(api_key.strip())
    if default_model is not None:
        row.default_model = default_model or None
    db.commit()
    apply_overrides(db)
    return next(p for p in list_providers(db) if p["provider"] == provider)


def probe(db: Session, provider: str) -> dict:
    """Live-test a provider's effective key. Returns {ok, status, latencyMs, error}."""
    # Custom providers have their own probe path
    if provider not in PROVIDER_KEY_ATTR:
        return probe_custom(db, provider)
    key = effective_key(db, provider)
    if not key:
        return {"ok": False, "status": None, "latencyMs": None, "error": "no key configured"}
    s = get_settings()
    t0 = time.time()
    try:
        if provider == "openrouter":
            r = httpx.get("https://openrouter.ai/api/v1/key",
                          headers={"Authorization": f"Bearer {key}"}, timeout=10)
        elif provider == "openai":
            r = httpx.get("https://api.openai.com/v1/models",
                          headers={"Authorization": f"Bearer {key}"}, timeout=10)
        elif provider == "anthropic":
            r = httpx.get("https://api.anthropic.com/v1/models",
                          headers={"x-api-key": key, "anthropic-version": "2023-06-01"}, timeout=10)
        elif provider == "google":
            r = httpx.get(f"https://generativelanguage.googleapis.com/v1beta/models?key={key}", timeout=10)
        elif provider == "dashscope":
            base = (s.dashscope_base_url or "https://dashscope-intl.aliyuncs.com/compatible-mode/v1").rstrip("/")
            r = httpx.get(f"{base}/models", headers={"Authorization": f"Bearer {key}"}, timeout=10)
        elif provider == "ollama":
            base = (s.ollama_base_url or "http://localhost:11434").rstrip("/")
            headers = {} if not key else {"Authorization": f"Bearer {key}"}
            r = httpx.get(f"{base}/api/tags", headers=headers, timeout=10)
        elif provider == "deepseek":
            r = httpx.get("https://api.deepseek.com/v1/models",
                          headers={"Authorization": f"Bearer {key}"}, timeout=10)
        else:
            return {"ok": False, "status": None, "latencyMs": None, "error": "unsupported"}
        ms = int((time.time() - t0) * 1000)
        ok = r.status_code == 200
        err = None if ok else (f"HTTP {r.status_code}" + (" (bad key)" if r.status_code in (401, 403) else ""))
        return {"ok": ok, "status": r.status_code, "latencyMs": ms, "error": err}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "status": None, "latencyMs": int((time.time() - t0) * 1000), "error": str(e)[:120]}


# ═══════════════════════════════════════════════════════════════════════════════
# Custom (OpenAI-compatible) provider management
# ═══════════════════════════════════════════════════════════════════════════════


def _custom_row(db: Session, slug: str) -> CustomLlmProvider | None:
    return db.get(CustomLlmProvider, (get_current_tenant(), slug))


def get_custom_provider(db: Session, slug: str) -> dict | None:
    """Public dict for admin endpoints. NEVER includes the decrypted key."""
    r = _custom_row(db, slug)
    if r is None:
        return None
    return {
        "provider": r.slug,
        "label": r.label,
        "baseUrl": r.base_url,
        "keySource": "custom" if r.api_key_enc else "none",
        "configured": bool(r.enabled and r.base_url),
        "enabled": r.enabled,
        "defaultModel": r.default_model,
        "custom": True,
    }


def list_custom_providers(db: Session) -> list[dict]:
    """All custom providers for the current tenant, sorted by slug."""
    rows = db.scalars(
        select(CustomLlmProvider)
        .where(CustomLlmProvider.tenant_id == get_current_tenant())
        .order_by(CustomLlmProvider.slug)
    ).all()
    return [get_custom_provider(db, r.slug) for r in rows]


def effective_custom_providers(db: Session) -> dict[str, dict]:
    """Return enabled custom provider configs for the gateway cache.
    Shape: {slug: {label, base_url, api_key}}. Keys are DECRYPTED."""
    tid = get_current_tenant()
    rows = db.scalars(
        select(CustomLlmProvider)
        .where(CustomLlmProvider.tenant_id == tid, CustomLlmProvider.enabled.is_(True))
    ).all()
    return {
        r.slug: {
            "label": r.label,
            "base_url": r.base_url,
            "api_key": _decrypt(r.api_key_enc) if r.api_key_enc else "",
        }
        for r in rows
    }


def set_custom_provider(
    db: Session,
    slug: str,
    *,
    label: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    clear_key: bool = False,
    enabled: bool | None = None,
    default_model: str | None = None,
    create: bool = False,
) -> dict:
    """Create or update a custom provider.  Raises ValueError on validation failure."""
    _validate_custom_slug(slug)
    tid = get_current_tenant()
    row = db.get(CustomLlmProvider, (tid, slug))
    if row is None:
        if not create:
            raise ValueError(f"custom provider {slug!r} does not exist")
        if not (label and base_url):
            raise ValueError("label and baseUrl are required")
        row = CustomLlmProvider(
            tenant_id=tid, slug=slug,
            label=label.strip(), base_url=base_url.strip().rstrip("/"),
            enabled=True,
        )
        db.add(row)
    if label is not None:
        row.label = label.strip()
    if base_url is not None:
        row.base_url = base_url.strip().rstrip("/")
    if clear_key:
        row.api_key_enc = None
    elif api_key:
        row.api_key_enc = _encrypt(api_key.strip())
    if enabled is not None:
        row.enabled = enabled
    if default_model is not None:
        row.default_model = default_model or None
    db.commit()
    _refresh_gateway_cache(db)
    return get_custom_provider(db, slug)


def delete_custom_provider(db: Session, slug: str) -> None:
    """Delete a custom provider and its routing-config model list.
    Existing operation overrides referencing {slug}/… survive and degrade
    gracefully to the stub (log warning)."""
    tid = get_current_tenant()
    row = db.get(CustomLlmProvider, (tid, slug))
    if row is None:
        raise ValueError(f"custom provider {slug!r} does not exist")
    db.delete(row)
    # Clean up the provider's model-list section from routing config so the
    # admin console doesn't show a ghost section.
    try:
        from app.repositories import routing_configs as _rc_repo
        rc = _rc_repo.get(db) or {}
        pm = rc.get("provider_models", {})
        if slug in pm:
            pm.pop(slug, None)
            rc["provider_models"] = pm
            _rc_repo.upsert(db, rc)
    except Exception:  # noqa: BLE001 — cleanup must never block deletion
        pass
    db.commit()
    _refresh_gateway_cache(db)


def probe_custom(db: Session, slug: str) -> dict:
    """Live-test a custom provider: GET {base_url}/models.  Returns
    {ok, status, latencyMs, error}."""
    t0 = time.time()
    try:
        r = _custom_row(db, slug)
        if r is None or not r.enabled:
            return {"ok": False, "status": None, "latencyMs": None,
                    "error": "provider not found or disabled"}
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if r.api_key_enc:
            headers["Authorization"] = f"Bearer {_decrypt(r.api_key_enc)}"
        resp = httpx.get(f"{r.base_url.rstrip('/')}/models", headers=headers, timeout=10)
        ms = int((time.time() - t0) * 1000)
        ok = resp.status_code == 200
        err = None if ok else (
            f"HTTP {resp.status_code}"
            + (" (bad key)" if resp.status_code in (401, 403) else "")
        )
        return {"ok": ok, "status": resp.status_code, "latencyMs": ms, "error": err}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "status": None,
                "latencyMs": int((time.time() - t0) * 1000), "error": str(e)[:120]}


def _refresh_gateway_cache(db: Session) -> None:
    """Push the effective custom-provider config into the gateway cache.
    Called after every create/update/delete — keeps gateway.cache in sync
    without the gateway importing the DB layer."""
    try:
        from app.llm import gateway
        entries = effective_custom_providers(db)
        gateway._set_custom_cache(entries, get_current_tenant())
    except Exception:  # noqa: BLE001
        pass  # cache refresh is best-effort; gateway self-heals via lazy TTL
