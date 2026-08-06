"""M43.P1 · BGE-Reranker-v2-m3 cross-encoder.

Open-source cross-encoder from BAAI · MIT license · best-in-class for
re-ranking dense + lexical hits. Applied after the hybrid (BM25 + cosine
RRF) retrieve to re-score top-K candidates against the query directly.
Cross-encoders score (query, doc) pairs in one forward pass with full
attention, vs. dual-encoder embedders that vectorize independently —
typically +25-40% precision@5 at the cost of latency.

Model
-----
BAAI/bge-reranker-v2-m3 · multilingual (100+ languages) · 568M params ·
~120MB ONNX · ~20ms per (query, chunk) pair on modern CPU. The right
choice when you don't have GPU.

Engine
------
Uses `sentence-transformers` (CrossEncoder API). Lazy-loaded singleton
per worker process — first call eats ~3s for model load, subsequent
calls hit the warm session. Falls back gracefully (no-op) when the
package isn't installed (dev / test envs without the dep).

Cache
-----
The HuggingFace hub cache lives at `/app/.cache/` in the container
(set HF_HOME at boot). We pre-download the model in the Dockerfile so
cold-start latency stays bounded.
"""

from __future__ import annotations

import logging
import os
from typing import Any

log = logging.getLogger("docaiq.reranker")

# Override via env if you need a different reranker (e.g. cohere · cohere
# rerank via API is heavier dep, mxbai-rerank-large-v1 for higher recall,
# bge-reranker-v2-gemma for top quality at higher cost).
# CPU default: ms-marco-MiniLM-L-6-v2 (22M) reranks 20 pairs in ~1.3s on CPU and correctly
# discriminates (verified). BAAI/bge-reranker-v2-m3 (568M, multilingual) is higher quality but
# ~37s/query on CPU — too slow for interactive chat; use it only with a GPU (set the env var).
_MODEL_NAME = os.environ.get("DOCAIQ_RERANKER_MODEL", "cross-encoder/ms-marco-MiniLM-L-6-v2")

_engine: Any = None  # Singleton CrossEncoder instance


def _get_engine() -> Any:
    """Lazy-load BGE reranker. Returns None when the dep isn't installed
    so the caller can fall back silently."""
    global _engine
    if _engine is not None:
        return _engine
    try:
        from sentence_transformers import CrossEncoder  # type: ignore
    except ImportError as e:
        log.info("reranker: sentence-transformers not installed (%s) · fallback to identity", e)
        return None
    try:
        # CPU device; trust_remote_code not needed for bge-reranker-v2-m3.
        # max_length=512 keeps inputs reasonable for short chunks +
        # query, but long-chunk pairs get auto-truncated.
        _engine = CrossEncoder(_MODEL_NAME, max_length=512, device="cpu")
        log.info("reranker: loaded %s", _MODEL_NAME)
        return _engine
    except Exception as e:  # noqa: BLE001
        log.warning("reranker: failed to load %s: %s · fallback to identity", _MODEL_NAME, e)
        return None


def is_available() -> bool:
    """Cheap probe — does the dep import cleanly? Used by the retrieval
    layer to decide whether to skip the initial-pool inflation."""
    try:
        import sentence_transformers  # type: ignore  # noqa: F401
        return True
    except ImportError:
        return False


def rerank(query: str, candidates: list[tuple[int, str]]) -> list[tuple[int, float]]:
    """Re-score (chunk_pk, chunk_text) pairs against the query.

    Returns [(chunk_pk, score), ...] sorted DESCending by reranker score.
    Higher = more relevant. Scores are raw cross-encoder logits (not
    normalized) — useful for relative comparison, not as a probability.

    Fail-open: when the engine isn't available, returns the input order
    unchanged with constant score (0.0). Caller's retrieval will then
    fall back to RRF order without any visible error.
    """
    if not candidates:
        return []

    engine = _get_engine()
    if engine is None:
        # Identity rerank · preserve input order
        return [(pk, 0.0) for pk, _ in candidates]

    pairs = [(query, text or "") for _, text in candidates]
    try:
        scores = engine.predict(pairs)  # numpy array of floats
    except Exception as e:  # noqa: BLE001
        log.warning("reranker: predict failed: %s · fallback to identity", e)
        return [(pk, 0.0) for pk, _ in candidates]

    paired = [(candidates[i][0], float(scores[i])) for i in range(len(candidates))]
    paired.sort(key=lambda kv: kv[1], reverse=True)
    return paired
