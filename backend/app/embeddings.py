"""Embedding backend.

Two implementations behind one `embed(texts)` interface:

* `hash`   — deterministic 384d feature-hash vector. No external deps. Useless
             for real retrieval (no semantic structure), but the pipeline
             produces *something* deterministic so M7 plumbing can be verified
             end-to-end and M8 retrieval gets exercised with real SQL. Default.

* `openai` — calls OpenAI's embeddings endpoint via httpx. Activated when
             `DOCAIQ_OPENAI_API_KEY` is set; returns text-embedding-3-small
             vectors (truncated/padded to `embed_dim` if you've overridden it).

The choice is a config flip — switching backends requires re-ingesting because
embeddings from different models aren't comparable. The DB column is fixed-
dim at boot, so changing `embed_dim` requires a migration. Pick once.

Backend selection can be controlled via the admin UI (routing_config DB) or
env vars (DOCAIQ_EMBED_BACKEND / DOCAIQ_EMBED_V2_BACKEND). DB takes precedence.
"""

from __future__ import annotations

import hashlib
import logging
import math
import os as _os
from typing import Iterable

import httpx

from app.config import get_settings

log = logging.getLogger("docaiq.embeddings")

# ── Embedding config cache (DB-driven, env-var fallback) ──────────────────
# Populated at boot by main.py's lifespan (backend) or on first use (worker).
# Invalidated by superadmin when the admin saves embedding config.

_embedding_config_cache: dict = {}  # tenant_id → {v1_backend, v2_backend, v2_active, operations_model_overrides}


def _read_embedding_config() -> dict:
    """Read the embedding section of routing_config for the current tenant.
    Returns empty dict on any failure (cold cache, missing config, etc.).
    The caller falls back to env vars."""
    try:
        from app.db import get_current_tenant
        tid = get_current_tenant()
        if tid and tid in _embedding_config_cache:
            return _embedding_config_cache[tid]
    except Exception:
        pass
    return {}


def get_cached_embedding_config() -> dict:
    """Public reader — returns the cached config for the current tenant, or {}."""
    return _read_embedding_config()


def _refresh_embedding_config_cache(db) -> None:
    """Warm the embedding config cache from DB. Called at boot + on admin save."""
    from app.db import get_current_tenant
    from app.repositories import routing_configs as rc_repo
    try:
        tid = get_current_tenant()
        rc = rc_repo.get(db)
        emb = (rc or {}).get("embedding", {}) if isinstance(rc, dict) else {}
        ops = (rc or {}).get("operations", {}) if isinstance(rc, dict) else {}
        _embedding_config_cache[tid] = {
            "v1_backend": emb.get("v1_backend", ""),
            "v2_backend": emb.get("v2_backend", ""),
            "v2_active": emb.get("v2_active", True),
            "operations": ops,
        }
    except Exception:
        log.warning("embeddings: config cache warm failed (non-fatal)")


def invalidate_embedding_config_cache(tenant_id: str | None = None) -> None:
    """Clear the cache so the next call re-reads from DB."""
    if tenant_id:
        _embedding_config_cache.pop(tenant_id, None)
    else:
        _embedding_config_cache.clear()


def _get_embed_backend_v1() -> str:
    """Return the active v1 embedding backend. DB config → env var → default."""
    cfg = _read_embedding_config()
    if cfg.get("v1_backend"):
        return cfg["v1_backend"]
    return get_settings().embed_backend


def _get_embed_backend_v2() -> str:
    """Return the active v2 embedding backend. DB config → env var → default."""
    cfg = _read_embedding_config()
    if cfg.get("v2_backend"):
        return cfg["v2_backend"]
    return get_settings().embed_v2_backend


def _get_embed_v2_active() -> bool:
    """Return whether the v2 embedding pipeline is active."""
    cfg = _read_embedding_config()
    if "v2_active" in cfg:
        return bool(cfg["v2_active"])
    return get_settings().embed_v2_active


def _get_embed_model(op_id: str, settings_attr: str, fallback: str = "") -> str:
    """Return the effective model for an embedding op.
    routing_config.operations override → env var → hardcoded fallback."""
    cfg = _read_embedding_config()
    ops = cfg.get("operations", {})
    if op_id in ops:
        override = ops[op_id]
        model = override.get("model") if isinstance(override, dict) else override
        if model:
            return model
    # Env var fallback
    if settings_attr:
        env_val = _os.getenv(settings_attr, "").strip()
        if env_val:
            return env_val
    return fallback


# ---- Public API -------------------------------------------------------------
def embed(texts: list[str]) -> list[list[float]]:
    """Embed a batch of strings. Returns one vector per input.
    Backend resolved from: admin DB config → DOCAIQ_EMBED_BACKEND env var."""
    backend = _get_embed_backend_v1()
    if backend == "hash":
        return [_hash_embed(t) for t in texts]
    if backend == "openai":
        return _openai_embed(texts)
    if backend == "gemini":
        return _gemini_embed(texts)
    if backend == "dashscope":
        return _dashscope_embed(texts)
    if backend == "local":
        return _local_embed(texts)
    raise ValueError(f"Unknown embedding backend: {backend!r}")


# ---- Dimension coercion + boot probe ---------------------------------------
# Every backend must emit vectors of exactly `embed_dim` (the fixed pgvector
# column width). A model whose native width differs would otherwise be SILENTLY
# truncated/padded here → vectors land in a different space than the index was
# built on → cosine retrieval quietly returns garbage with no error. We funnel
# all backends through `_coerce_dim` so the coercion is (a) in one place and
# (b) LOUD (one WARNING per observed mismatch), and expose `assert_embed_dim()`
# for a fail-fast boot probe.
_last_native_dim: int | None = None  # native width the active backend last produced
_warned_native_dims: set[tuple[str, int, int]] = set()


def _coerce_dim(vecs: Iterable[list[float]], target: int, *, backend: str) -> list[list[float]]:
    global _last_native_dim
    out: list[list[float]] = []
    for v in vecs:
        v = list(v)
        _last_native_dim = len(v)
        if len(v) != target:
            key = (backend, len(v), target)
            if key not in _warned_native_dims:
                _warned_native_dims.add(key)
                log.warning(
                    "embeddings: backend %s emits %dd vectors but embed_dim=%d — "
                    "%s to fit. Retrieval quality WILL degrade; align the model and "
                    "the pgvector column dim (see assert_embed_dim).",
                    backend, len(v), target,
                    "truncating" if len(v) > target else "zero-padding",
                )
            v = v[:target] if len(v) > target else v + [0.0] * (target - len(v))
        out.append(v)
    return out


def assert_embed_dim() -> None:
    """Boot probe: fail fast if the active backend's NATIVE vector width does not
    match DOCAIQ_EMBED_DIM. Without it, a model/dim mismatch is silently coerced
    in `_coerce_dim` and corrupts retrieval invisibly. Safe to call at app/worker
    startup: a transient error (missing API key, network blip, model not
    installed) is logged and skipped — only a CONFIRMED native≠target is fatal."""
    s = get_settings()
    target = s.embed_dim
    backend = _get_embed_backend_v1()
    if backend == "hash":
        return  # constructs vectors at exactly embed_dim by definition
    try:
        embed(["dimension probe"])  # records _last_native_dim via _coerce_dim
    except Exception as e:  # noqa: BLE001
        log.warning("embeddings: dim probe could not run (%s); skipping assert", e)
        return
    native = _last_native_dim
    if native is not None and native != target:
        raise RuntimeError(
            f"Embedding dim mismatch: backend={backend!r} emits {native}d vectors but "
            f"DOCAIQ_EMBED_DIM={target}. They would be silently "
            f"{'truncated' if native > target else 'zero-padded'} → corrupt retrieval. "
            f"Set DOCAIQ_EMBED_DIM={native} and migrate the pgvector column, or pick a "
            f"{target}d-native model."
        )


def embed_signature() -> str:
    """A string identifying the embedding model that produced a vector, e.g.
    'local:sentence-transformers/all-MiniLM-L6-v2:384'. Stamped into workspace
    snapshots so a restore can REFUSE to reuse vectors from a different model
    (same dimension ≠ same vector space) and re-ingest instead."""
    s = get_settings()
    b = _get_embed_backend_v1()
    model = {
        "local": _get_embed_model("embed_v1_local", "DOCAIQ_LOCAL_EMBED_MODEL", s.local_embed_model),
        "openai": _get_embed_model("embed_v1_openai", "DOCAIQ_OPENAI_EMBED_MODEL", s.openai_embed_model),
        "gemini": _get_embed_model("embed_v1_google", "", "gemini-embedding-001"),
        "dashscope": _get_embed_model("embed_v1_dashscope", "DOCAIQ_DASHSCOPE_EMBED_MODEL", s.dashscope_embed_model),
        "hash": "hash",
    }.get(b, b)
    return f"{b}:{model}:{s.embed_dim}"


# ---- Hash backend ----------------------------------------------------------
def _hash_embed(text: str) -> list[float]:
    """Feature-hash a string into a fixed-dim L2-normalized vector.

    Algorithm: tokenize on whitespace + punctuation; for each token, hash to
    a slot index and a sign, accumulate. Stable across runs. Not semantically
    meaningful — collisions are random — but deterministic and free.
    """
    dim = get_settings().embed_dim
    vec = [0.0] * dim
    for token in _tokenize(text):
        h = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        idx = int.from_bytes(h[:4], "little") % dim
        sign = 1.0 if (h[4] & 1) else -1.0
        vec[idx] += sign
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def _tokenize(text: str) -> Iterable[str]:
    """Cheap tokenizer — lowercase, split on non-alphanumeric, drop short tokens."""
    cur: list[str] = []
    for ch in text.lower():
        if ch.isalnum():
            cur.append(ch)
        else:
            if len(cur) >= 2:
                yield "".join(cur)
            cur.clear()
    if len(cur) >= 2:
        yield "".join(cur)


# ---- Local backend (sentence-transformers · no API key, no per-call cost) --
# Best for cost at scale: a small CPU model runs in-process, so embedding
# 1000s of docs/chat-queries costs nothing per call. all-MiniLM-L6-v2 emits
# 384d natively — exactly the default pgvector column dim — and ships with the
# same sentence-transformers dep the reranker already uses.
_st_model = None  # singleton SentenceTransformer


def _get_st_model():
    global _st_model
    if _st_model is not None:
        return _st_model
    from sentence_transformers import SentenceTransformer  # type: ignore
    name = _get_embed_model("embed_v1_local", "DOCAIQ_LOCAL_EMBED_MODEL",
                            get_settings().local_embed_model)
    _st_model = SentenceTransformer(name, device="cpu")
    log.info("embeddings: loaded local model %s", name)
    return _st_model


# ---- V2 embedder (BGE-M3, 1024d) — dual-column migration, Retrieval Step 2 --
# Separate singleton + column from the live MiniLM path, so we can backfill + A/B
# without touching the serving embeddings. Isolated per-text here (Phase 2a);
# Phase 2b (late chunking) will pool token spans within the full document instead.
_st_model_v2 = None


def _get_st_model_v2():
    global _st_model_v2
    if _st_model_v2 is not None:
        return _st_model_v2
    from sentence_transformers import SentenceTransformer  # type: ignore
    name = _get_embed_model("embed_v2_local", "DOCAIQ_EMBED_V2_MODEL",
                            get_settings().embed_v2_model)
    _st_model_v2 = SentenceTransformer(name, device="cpu")
    log.info("embeddings: loaded v2 model %s", name)
    return _st_model_v2


def embed_v2(texts: list[str], backend: str | None = None) -> list[list[float]]:
    """1024d embeddings for document_chunks.embedding_v2. Backend chosen by embed_v2_backend:
    'dashscope' = hosted text-embedding-v4 (1024d native, CPU-friendly, no GPU); 'local' = BGE-M3
    via sentence-transformers. Dim coerced to embed_v2_dim and flagged LOUD on a native mismatch.

    Pass ``backend`` to override the global setting per-document (used by the DocumentStrategist
    to route large docs to dashscope while keeping small docs on local BGE-M3)."""
    if not texts:
        return []
    s = get_settings()
    target = s.embed_v2_dim
    use_backend = backend or _get_embed_backend_v2()
    if use_backend == "dashscope":
        return _dashscope_embed_dim(texts, target)
    model = _get_st_model_v2()
    vecs = model.encode([t or " " for t in texts], normalize_embeddings=True,
                        convert_to_numpy=True, batch_size=16)
    return _coerce_dim((v.tolist() for v in vecs), target, backend="local-v2")


def _dashscope_embed_dim(texts: list[str], target: int) -> list[list[float]]:
    """DashScope text-embedding-v4 (1024d native) → L2-normalized vectors coerced to `target`.
    Same OpenAI-compatible endpoint + DASHSCOPE_API_KEY as the chat gateway (no separate provider).
    Batched at the intl cap of 10 inputs/request. Used for the v2 (1024d) column, so with
    embed_v2_dim=1024 there is no truncation."""
    import math as _math
    s = get_settings()
    if not s.dashscope_api_key:
        raise RuntimeError("DOCAIQ_DASHSCOPE_API_KEY is empty but embed_v2_backend=dashscope")
    model = _get_embed_model("embed_v2_dashscope", "DOCAIQ_DASHSCOPE_EMBED_MODEL",
                             s.dashscope_embed_model)
    url = s.dashscope_base_url.rstrip("/") + "/embeddings"
    headers = {"Authorization": f"Bearer {s.dashscope_api_key}", "Content-Type": "application/json"}
    out: list[list[float]] = []
    for i in range(0, len(texts), 10):
        batch = [t or " " for t in texts[i:i + 10]]
        r = httpx.post(url, json={"model": model, "input": batch},
                       headers=headers, timeout=60)
        r.raise_for_status()
        raw = [item["embedding"] for item in r.json().get("data", [])]
        # L2-normalize so cosine == dot (matches the local path's normalize_embeddings=True)
        norm = []
        for v in raw:
            n = _math.sqrt(sum(x * x for x in v)) or 1.0
            norm.append([x / n for x in v])
        out.extend(_coerce_dim(norm, target, backend="dashscope-v2"))
    return out


def _local_embed(texts: list[str]) -> list[list[float]]:
    target = get_settings().embed_dim
    model = _get_st_model()
    vecs = model.encode([t or " " for t in texts], normalize_embeddings=True,
                        convert_to_numpy=True, batch_size=32)
    return _coerce_dim((v.tolist() for v in vecs), target, backend="local")


# ---- OpenAI backend --------------------------------------------------------
def _openai_embed(texts: list[str]) -> list[list[float]]:
    settings = get_settings()
    if not settings.openai_api_key:
        raise RuntimeError("DOCAIQ_OPENAI_API_KEY is empty but embed_backend=openai")

    target = settings.embed_dim
    model = _get_embed_model("embed_v1_openai", "DOCAIQ_OPENAI_EMBED_MODEL",
                             settings.openai_embed_model)
    # text-embedding-3-small + text-embedding-3-large support OpenAI's native
    # `dimensions` parameter — model emits a vector trained to be optimally
    # reducible to that size. Strictly better than post-hoc truncation.
    # text-embedding-ada-002 (legacy) ignores it and returns 1536d, so we
    # only send the param for v3 models.
    payload: dict = {"model": model, "input": texts}
    if "text-embedding-3" in model:
        payload["dimensions"] = target

    response = httpx.post(
        "https://api.openai.com/v1/embeddings",
        json=payload,
        headers={"Authorization": f"Bearer {settings.openai_api_key}"},
        timeout=60,
    )
    response.raise_for_status()
    data = response.json()["data"]
    # Fallback coercion for ada-002 or if the `dimensions` param was ignored.
    return _coerce_dim((item["embedding"] for item in data), target, backend="openai")


# ---- Dashscope backend (M44.P9.1) ------------------------------------------
def _dashscope_embed(texts: list[str]) -> list[list[float]]:
    """Alibaba Dashscope text embeddings via the OpenAI-compatible endpoint.

    Uses the same DASHSCOPE_API_KEY as the LLM gateway so users who already
    have Dashscope configured for chat get real semantic embeddings without
    a separate provider. Default model `text-embedding-v4` supports up to
    8192 input tokens per text and accepts a `dimensions` parameter (matches
    OpenAI v3 behavior) so we can match the pgvector column dim exactly.

    Batched: 25 inputs per call (Dashscope cap). Returns one vector per input.
    """
    settings = get_settings()
    if not settings.dashscope_api_key:
        raise RuntimeError("DOCAIQ_DASHSCOPE_API_KEY is empty but embed_backend=dashscope")
    target = settings.embed_dim
    model = _get_embed_model("embed_v1_dashscope", "DOCAIQ_DASHSCOPE_EMBED_MODEL",
                             settings.dashscope_embed_model)
    base = settings.dashscope_base_url.rstrip("/")
    url = f"{base}/embeddings"
    headers = {
        "Authorization": f"Bearer {settings.dashscope_api_key}",
        "Content-Type": "application/json",
    }
    # Dashscope caps batch size at 10 inputs per request on the intl
    # compatible-mode endpoint (verified 2026-05; >10 returns 400 with
    # `batch size is invalid, it should not be larger than 10`).
    # `dimensions` parameter is also NOT supported on intl as of
    # 2026-05; v3/v4 return 1024d vectors natively. We post-hoc truncate
    # to `embed_dim` (default 384) to match the pgvector column. Lossy
    # but still vastly better than `hash` for semantic retrieval.
    BATCH = 10
    out: list[list[float]] = []
    for i in range(0, len(texts), BATCH):
        batch = [t or " " for t in texts[i:i + BATCH]]
        payload: dict = {"model": model, "input": batch}
        r = httpx.post(url, json=payload, headers=headers, timeout=60)
        r.raise_for_status()
        out.extend(_coerce_dim(
            (item["embedding"] for item in r.json().get("data", [])),
            target, backend="dashscope"))
    return out


# ---- Gemini backend --------------------------------------------------------
def _gemini_embed(texts: list[str]) -> list[list[float]]:
    """Google Gemini direct embedding via gemini-embedding-001. Free tier:
    1500 RPM. Returns up to 3072d by default but supports `outputDimensionality`
    for projection to a smaller size — we use embed_dim to keep the pgvector
    column schema stable."""
    settings = get_settings()
    if not settings.google_genai_api_key:
        raise RuntimeError("DOCAIQ_GOOGLE_GENAI_API_KEY is empty but embed_backend=gemini")
    target = settings.embed_dim
    out: list[list[float]] = []
    # batchEmbedContents lets us send up to 100 docs per call. The free tier
    # is 1500 RPM so this matters when re-embedding large corpora.
    model = "gemini-embedding-001"
    BATCH = 100
    for i in range(0, len(texts), BATCH):
        batch = texts[i:i + BATCH]
        r = httpx.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:batchEmbedContents"
            f"?key={settings.google_genai_api_key}",
            json={
                "requests": [
                    {
                        "model": f"models/{model}",
                        "content": {"parts": [{"text": t or " "}]},
                        "outputDimensionality": target,
                    }
                    for t in batch
                ],
            },
            timeout=120,
        )
        r.raise_for_status()
        out.extend(_coerce_dim(
            (emb["values"] for emb in r.json().get("embeddings", [])),
            target, backend="gemini"))
    return out
