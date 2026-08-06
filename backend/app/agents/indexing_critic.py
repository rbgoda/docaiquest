"""M47 · Indexing Quality Critic — LLM-powered evaluation of document indexing.

Runs after ingestion completes. Evaluates chunk quality, entity extraction,
embedding relevance, and language handling. Uses a cheap LLM (Qwen) to check
whether the indexed representation faithfully captures the document content.

Architecture:
  1. Sample N chunks from the document (head, middle, tail)
  2. For each chunk, ask Qwen: "Does this chunk represent a coherent semantic unit?"
  3. Evaluate entity extraction: "Are these entities correctly extracted from the text?"
  4. Language detection + mixed-language flagging
  5. Score the overall indexing quality (0-100)
  6. Store critique in document_artifacts for inspection

Cost: 1-3 cheap LLM calls per document (Qwen 2.5-7B, ~$0.0001/call).
Gate: only runs when documents_indexing_critic=true (default off for cost control).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.feature_flags import is_enabled
from app.llm import gateway
from app.model_registry import REGISTRY as _AI_REGISTRY

log = logging.getLogger("docaiq.indexing_critic")

# ── Language detection (lightweight, no LLM) ──────────────────────────────
# Unicode ranges for common scripts. Used to flag mixed-language documents
# before the LLM critique runs.

_SCRIPT_RANGES: dict[str, tuple[int, int]] = {
    "Latin": (0x0041, 0x024F),
    "CJK": (0x4E00, 0x9FFF),
    "Hiragana": (0x3040, 0x309F),
    "Katakana": (0x30A0, 0x30FF),
    "Hangul": (0xAC00, 0xD7AF),
    "Arabic": (0x0600, 0x06FF),
    "Devanagari": (0x0900, 0x097F),
    "Thai": (0x0E00, 0x0E7F),
    "Cyrillic": (0x0400, 0x04FF),
}


def detect_languages(text: str) -> dict[str, float]:
    """Return {script_name: fraction_of_chars} for the top scripts in text."""
    if not text:
        return {}
    counts: dict[str, int] = {}
    total = 0
    for ch in text:
        cp = ord(ch)
        for script, (lo, hi) in _SCRIPT_RANGES.items():
            if lo <= cp <= hi:
                counts[script] = counts.get(script, 0) + 1
                total += 1
                break
    if total == 0:
        return {}
    return {s: round(c / total, 3) for s, c in sorted(counts.items(), key=lambda x: -x[1])}


def is_mixed_language(langs: dict[str, float], threshold: float = 0.1) -> bool:
    """True if more than one script has >= threshold fraction of characters."""
    return sum(1 for v in langs.values() if v >= threshold) >= 2


from app.llm.prompts import get_prompt

# ── Critique prompt ───────────────────────────────────────────────────────
_CRITIQUE_SYSTEM = """\
You are a document indexing quality auditor. You evaluate whether a document's \
chunks, entities, and metadata faithfully represent the original content for \
retrieval. Score each dimension 1-10 and provide a brief explanation.

Output ONLY a JSON object with these keys:
  "chunk_coherence": 1-10 — are chunks semantically coherent units?
  "entity_accuracy": 1-10 — are extracted entities correct and complete?
  "language_handling": 1-10 — is multi-language content properly captured?
  "searchability": 1-10 — would keyword + semantic search find these chunks?
  "overall": 1-10 — aggregate score
  "issues": [] — list of specific problems found (empty if none)
  "suggestions": [] — list of actionable improvements (empty if none)

Be strict but fair. Flag: split paragraphs, broken table rows, missing entities, \
garbled OCR text, language mixing without marking."""


@dataclass
class IndexingCritique:
    chunk_coherence: int = 0
    entity_accuracy: int = 0
    language_handling: int = 0
    searchability: int = 0
    overall: int = 0
    issues: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    languages_detected: dict[str, float] = field(default_factory=dict)
    mixed_language: bool = False
    raw_json: dict | None = None


def run(
    db: Session,
    doc_name: str,
    doc_type: str,
    chunk_texts: list[str],
    entities: list[dict],
    *,
    tenant_id: str,
    sample_size: int = 8,
) -> IndexingCritique | None:
    """Evaluate indexing quality for one document. Returns None on LLM failure.

    Args:
        db: DB session
        doc_name: Document filename
        doc_type: Classified document type
        chunk_texts: First N chunk texts from the document
        entities: Extracted entities [{kind, text, canonical}, ...]
        tenant_id: Tenant for LLM routing
        sample_size: How many chunks to sample (head/middle/tail)
    """
    from app.config import get_settings
    if not is_enabled("documents_indexing_critic", False):
        return None

    # Sample chunks: head 3, middle 2, tail 3
    n = len(chunk_texts)
    if n == 0:
        return None
    if n <= sample_size:
        sampled = chunk_texts
    else:
        head = chunk_texts[: min(3, n)]
        mid_start = max(3, n // 2 - 1)
        middle = chunk_texts[mid_start : mid_start + 2]
        tail = chunk_texts[-min(3, n - mid_start - 2) :]
        sampled = head + middle + tail

    # Build the evaluation prompt
    chunks_block = "\n\n---\n\n".join(
        f"[Chunk {i+1}]\n{t[:600]}" for i, t in enumerate(sampled)
    )
    entities_block = "\n".join(
        f"  · {e.get('kind','?')}: {e.get('text','')} → {e.get('canonical','')}"
        for e in (entities or [])[:30]
    ) or "(none)"

    # Language detection (free)
    full_text = " ".join(chunk_texts[:20])
    langs = detect_languages(full_text)
    mixed = is_mixed_language(langs)

    user = (
        f"Document: {doc_name} (type: {doc_type})\n"
        f"Languages detected: {langs or '(all ASCII)'}  Mixed: {mixed}\n\n"
        f"=== SAMPLED CHUNKS ===\n{chunks_block}\n\n"
        f"=== EXTRACTED ENTITIES ===\n{entities_block}\n\n"
        f"Evaluate the indexing quality. JSON:"
    )

    try:
        model = _AI_REGISTRY["indexing_critic"].default_model  # Qwen is strong on multilingual eval
        msgs = [
            gateway.Message(role="system", content=get_prompt("indexing_critic")),
            gateway.Message(role="user", content=user),
        ]
        result = gateway.call(model=model, messages=msgs, temperature=0.0, max_tokens=400)
        txt = (result.text or "").strip()
        # Tolerant JSON parse
        txt = re.sub(r"^```(?:json)?|```$", "", txt, flags=re.I | re.M).strip()
        data = json.loads(txt)
    except Exception as e:
        log.warning("indexing_critic: LLM call failed for %s: %s", doc_name, e)
        return None

    if not isinstance(data, dict):
        return None

    def _clamp(v, lo=1, hi=10):
        try:
            return max(lo, min(hi, int(v)))
        except (TypeError, ValueError):
            return 0

    critique = IndexingCritique(
        chunk_coherence=_clamp(data.get("chunk_coherence", 0)),
        entity_accuracy=_clamp(data.get("entity_accuracy", 0)),
        language_handling=_clamp(data.get("language_handling", 0)),
        searchability=_clamp(data.get("searchability", 0)),
        overall=_clamp(data.get("overall", 0)),
        issues=[str(i) for i in (data.get("issues") or [])[:10]],
        suggestions=[str(s) for s in (data.get("suggestions") or [])[:10]],
        languages_detected=langs,
        mixed_language=mixed,
        raw_json=data,
    )

    log.info(
        "indexing_critic: %s → overall=%d coherence=%d entities=%d lang=%d "
        "search=%d mixed=%s issues=%d",
        doc_name, critique.overall, critique.chunk_coherence,
        critique.entity_accuracy, critique.language_handling,
        critique.searchability, mixed, len(critique.issues),
    )
    return critique


def run_fast(doc_name: str, doc_type: str, chunk_texts: list[str]) -> dict | None:
    """Zero-LLM fast check: language detection + basic heuristics only.
    Returns a lightweight dict suitable for storage in document_artifacts.
    Always runs (no cost gate)."""
    full_text = " ".join(chunk_texts[:30])
    langs = detect_languages(full_text)
    mixed = is_mixed_language(langs)

    issues = []
    if mixed:
        issues.append(f"Mixed-language document: {langs}")
    if not chunk_texts:
        issues.append("No chunks produced")
    elif max(len(t) for t in chunk_texts[:10]) < 50:
        issues.append("Very short chunks — may indicate parsing failure")
    if any(len(t) > 5000 for t in chunk_texts[:10]):
        issues.append("Very large chunks — consider reducing chunk size")

    return {
        "languages": langs,
        "mixed_language": mixed,
        "chunk_count": len(chunk_texts),
        "avg_chunk_len": round(sum(len(t) for t in chunk_texts[:20]) / max(1, min(20, len(chunk_texts)))),
        "issues": issues,
        "source": "fast",
    }
