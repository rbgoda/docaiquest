"""OCR / page text-quality heuristics (Reducto-parity G3, scorer foundation).

Pure-stdlib (no fitz / LLM / DB) so it unit-tests offline. Produces a 0..1
quality score for a page's extracted/transcribed text, plus human-readable
flags, so the pipeline can surface "N pages look low-confidence — review"
(esp. for scanned / photographed documents where OCR can garble).

This module is the deterministic foundation. Wiring it into `ingestion_vision`
(score each OCR'd page), persisting the score, and surfacing it in the
Intelligence dashboard is the remaining G3 work (needs a DB migration + the
live vision path) — tracked in docs/REDUCTO_PARITY_ROADMAP.md.

Score interpretation: 1.0 = clean text; < ~0.55 = likely needs human review.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from statistics import fmean

_REPLACEMENT = "�"  # the '�' the decoder emits for undecodable bytes

# Pages scoring below this are flagged as review candidates.
REVIEW_THRESHOLD = 0.55


@dataclass(frozen=True)
class PageQuality:
    score: float
    flags: list[str] = field(default_factory=list)
    metrics: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"score": self.score, "flags": list(self.flags), "metrics": dict(self.metrics)}


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def page_quality(text: str) -> PageQuality:
    """Score a page's text 0..1 (higher = cleaner) with explanatory flags."""
    text = text or ""
    n = len(text)
    if not text.strip():
        return PageQuality(0.0, ["empty"], {"chars": 0.0})

    non_space = [c for c in text if not c.isspace()]
    n_ns = len(non_space)
    if n_ns < 10:
        return PageQuality(0.1, ["near_empty"], {"chars": float(n_ns)})

    repl_ratio = text.count(_REPLACEMENT) / n
    printable_ratio = sum(1 for c in text if c.isprintable() or c.isspace()) / n
    alnum_ratio = sum(1 for c in non_space if c.isalnum()) / n_ns
    tokens = text.split()
    avg_tok = fmean(len(t) for t in tokens) if tokens else 0.0
    long_token_ratio = (sum(1 for t in tokens if len(t) > 30) / len(tokens)) if tokens else 0.0

    score = 1.0
    flags: list[str] = []

    if repl_ratio > 0.005:
        score -= min(0.6, repl_ratio * 4.0)
        flags.append("replacement_chars")
    if printable_ratio < 0.98:
        score -= min(0.5, (1.0 - printable_ratio) * 3.0)
        flags.append("non_printable")
    if alnum_ratio < 0.55:
        # A page that is mostly punctuation/symbols is usually OCR garbage.
        score -= min(0.5, (0.55 - alnum_ratio) * 1.8)
        flags.append("low_alpha")
    if long_token_ratio > 0.10:
        # Many 30+ char "words" → run-together OCR with no spacing recovered.
        score -= min(0.35, long_token_ratio * 1.2)
        flags.append("run_on_tokens")
    if avg_tok < 1.6:
        # Fragmented into single chars → spacing exploded.
        score -= 0.2
        flags.append("fragmented")

    metrics = {
        "chars": float(n),
        "replacement_ratio": round(repl_ratio, 4),
        "printable_ratio": round(printable_ratio, 4),
        "alnum_ratio": round(alnum_ratio, 4),
        "avg_token_len": round(avg_tok, 2),
        "long_token_ratio": round(long_token_ratio, 4),
    }
    return PageQuality(round(_clamp(score), 4), flags, metrics)


def is_low_confidence(text: str, threshold: float = REVIEW_THRESHOLD) -> bool:
    """True when the page text scores below `threshold` (review candidate)."""
    return page_quality(text).score < threshold


def summarize_pages(pages: list[tuple[int, str]], scored_page_nums,
                    *, threshold: float = REVIEW_THRESHOLD,
                    max_pages: int = 50) -> dict | None:
    """Score the OCR'd pages and return an aggregate summary (or None if none
    were scored). `pages` is [(page_no, text), ...]; `scored_page_nums` is the
    set of page numbers that went through OCR — only those are scored, since
    PyMuPDF text pages are assumed clean.

    Shape (mirrors the `documents.ocr_quality` JSONB column):
      {pagesScored, lowConfidencePages, minScore, flagged, threshold,
       pages:[{page, score, flags}]}
    """
    want = set(scored_page_nums or ())
    scored: list[dict] = []
    for page_no, text in pages:
        if page_no in want:
            q = page_quality(text or "")
            scored.append({"page": page_no, "score": q.score, "flags": q.flags})
    if not scored:
        return None
    low = [s for s in scored if s["score"] < threshold]
    return {
        "pagesScored": len(scored),
        "lowConfidencePages": len(low),
        "minScore": min(s["score"] for s in scored),
        "flagged": bool(low),
        "threshold": threshold,
        "pages": scored[:max_pages],
    }
