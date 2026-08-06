"""M43.P1 · Contextual Retrieval (Anthropic, Sep 2024).

For each chunk in a document, generate a ~50-100 token sentence that
situates the chunk in the document. Prepending that to the chunk text
before embedding lifts retrieval recall +35-49% in their published
evaluations. The Anthropic post:
  https://www.anthropic.com/news/contextual-retrieval

Why it works
------------
A standalone chunk like "the firm rotates this every 90 days" is
nearly impossible to retrieve — there's no signal about WHAT is rotated
or BY WHOM. With context "this chunk discusses the Acme Inc. SOC 2
access-review cadence policy", the chunk now embeds in a semantic
neighborhood where queries like "Acme access review frequency" or
"how often does the firm review access" hit it.

Design choices for DocAIQ
-------------------------
* Uses the cheap LLM cascade: prefer Qwen / Gemini Flash, fall back to
  Claude Haiku. Caller passes the model preference; we don't hard-code.
* Doc-level summary computed ONCE per doc and reused for all chunks
  (mirrors Anthropic's prompt-caching pattern in spirit).
* Skips chunks < 50 chars (not enough signal to contextualize) and caps
  at 50 chunks per doc (long docs stay bounded in cost).
* Fail-open: any LLM error returns an empty context string. Ingestion
  never blocks on this — the chunk just doesn't get the recall boost.
* Pure-functional API: takes raw strings, returns strings. The caller
  (app/ingestion.py) handles DB writes.
"""

from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor

from app.llm import gateway

log = logging.getLogger("docaiq.contextual")

# Cheap, fast models preferred. Override via env to use a specific model.
# OpenRouter routes; bare models fall through to provider-direct.
_DEFAULT_MODEL = os.environ.get(
    "DOCAIQ_CONTEXTUAL_MODEL",
    "dashscope/qwen-turbo",  # funded, fast, cheap — contextual retrieval is high-volume
)

# Per-doc summary template. Asks the LLM for a ~100-token description of
# the WHOLE document; that becomes the standing context every chunk
# inherits before being further contextualized.
_DOC_SUMMARY_SYSTEM = (
    "You are summarizing a document so its chunks can be searched well later. "
    "Write ONE sentence (≤80 tokens) describing what the document is and its "
    "key entities (org names, dates, amounts, jurisdictions, etc.). No filler. "
    "Just the sentence."
)

# Per-chunk context template. Given the doc summary + this specific chunk,
# write a ≤50 token "this chunk discusses X" sentence so a query that
# never mentions the chunk's exact words can still find it semantically.
_CHUNK_CONTEXT_SYSTEM = (
    "You are situating one chunk within a known document so a search "
    "system can find it. Given the document summary and the chunk, write "
    "ONE short sentence (≤50 tokens) starting with 'This chunk discusses' "
    "or 'This section covers'. Reference the doc's key entity if relevant. "
    "No filler."
)

# Skip chunks shorter than this — not enough content to situate
_MIN_CHUNK_CHARS = 50

# Cap at this many chunks per doc — bounds cost on very long docs
_MAX_CHUNKS_PER_DOC = 200  # raised from 50 — long docs need full contextual coverage


def _summarize_document(doc_text: str, doc_name: str, model: str) -> str:
    """One LLM call · the whole-doc summary that each chunk's context
    inherits. Truncates input to ~30K chars (~7K tokens) so we stay well
    under any single-call window. Returns empty string on failure."""
    # Anthropic's eval used full-doc context; we approximate by sampling
    # the head + tail for very long docs (preserves intro + conclusion).
    if len(doc_text) > 30_000:
        head = doc_text[:18_000]
        tail = doc_text[-10_000:]
        sample = f"{head}\n\n[... middle elided ...]\n\n{tail}"
    else:
        sample = doc_text
    user_prompt = (
        f"Document name: {doc_name}\n\n"
        f"Document text:\n{sample}\n\n"
        "Write the one-sentence summary now."
    )
    try:
        result = gateway.call(
            model,
            messages=[
                gateway.Message(role="system", content=_DOC_SUMMARY_SYSTEM),
                gateway.Message(role="user", content=user_prompt),
            ],
            temperature=0.0,
            max_tokens=160,
        )
        return (result.text or "").strip()
    except Exception as e:  # noqa: BLE001
        log.warning("contextual: doc summary failed for %r: %s", doc_name, e)
        return ""


def _contextualize_chunk(doc_summary: str, chunk_text: str, model: str) -> str:
    """One LLM call per chunk · the situating sentence prepended before
    embedding. Returns empty string on failure (fail-open)."""
    user_prompt = (
        f"Document summary: {doc_summary or '(no summary available)'}\n\n"
        f"Chunk text:\n{chunk_text}\n\n"
        "Write the one-sentence situating context now."
    )
    try:
        result = gateway.call(
            model,
            messages=[
                gateway.Message(role="system", content=_CHUNK_CONTEXT_SYSTEM),
                gateway.Message(role="user", content=user_prompt),
            ],
            temperature=0.0,
            max_tokens=80,
        )
        return (result.text or "").strip()
    except Exception as e:  # noqa: BLE001
        log.warning("contextual: chunk context failed: %s", e)
        return ""


def generate_contexts(
    doc_text: str,
    doc_name: str,
    chunk_texts: list[str],
    *,
    model: str | None = None,
    parallel: int = 4,
) -> list[str]:
    """Main entry. Returns a parallel list of context strings — one per
    input chunk_texts entry. Empty string when:
      * the chunk is too short (< 50 chars),
      * the chunk index is beyond the per-doc cap (50),
      * the LLM call failed.

    Caller is responsible for caching: don't call this on every re-ingest
    if the doc text is unchanged. The ingestion path handles that via the
    "replace chunks on re-ingest" pattern already in place.
    """
    model_id = model or _DEFAULT_MODEL
    n = len(chunk_texts)
    contexts: list[str] = [""] * n
    if n == 0:
        return contexts

    # 1. Doc-level summary · one call, used by all chunks
    doc_summary = _summarize_document(doc_text, doc_name, model_id)
    log.info("contextual: doc_summary len=%d for %r", len(doc_summary), doc_name)

    # 2. Per-chunk contexts in parallel · cheap-LLM batch
    work: list[tuple[int, str]] = []
    for i, ct in enumerate(chunk_texts):
        if i >= _MAX_CHUNKS_PER_DOC:
            break
        if len(ct) < _MIN_CHUNK_CHARS:
            continue
        work.append((i, ct))

    if not work:
        return contexts

    def _one(item: tuple[int, str]) -> tuple[int, str]:
        i, ct = item
        return (i, _contextualize_chunk(doc_summary, ct, model_id))

    with ThreadPoolExecutor(max_workers=parallel) as ex:
        for i, ctx in ex.map(_one, work):
            contexts[i] = ctx

    populated = sum(1 for c in contexts if c)
    log.info("contextual: %d/%d chunks contextualized (model=%s)", populated, n, model_id)
    return contexts


def embedding_input(chunk_text: str, context: str | None) -> str:
    """The string actually sent to the embedder. With context, we prepend
    the situating sentence so the embedding lives in a semantic space
    that includes the document context. Without context, we fall back to
    the raw chunk text (back-compat for pre-M43.P1 chunks).

    Caller pattern:
        vec = embed([embedding_input(c.text, c.context_summary) for c in chunks])
    """
    if context:
        return f"{context}\n\n{chunk_text}"
    return chunk_text
