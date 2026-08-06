"""Document Strategist — master agent that profiles a document at upload/ingestion
time and selects the best processing strategy.

Runs BEFORE the heavy ingestion pipeline so each stage (parse, chunk, embed, extract)
can adapt to the document's characteristics instead of using one-size-fits-all defaults.

Pure function — no I/O, no LLM calls, no DB. Fast, deterministic, testable.

Architecture
------------
  Upload bytes + MIME + size
         │
         ▼
  ┌──────────────────┐
  │ DocumentProfile  │  ← cheap (<1s): page count, text density sample
  └───────┬──────────┘
          │
          ▼
  ┌──────────────────┐
  │  strategize()    │  ← pure function: profile → strategy matrix lookup
  └───────┬──────────┘
          │
          ▼
  ┌──────────────────┐
  │ProcessingStrategy│  ← embed_backend, chunking_mode, ocr_mode, timeout, priority
  └───────┬──────────┘
          │
          ▼
  ingest_document() reads strategy.* and adapts each pipeline stage.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

log = logging.getLogger("docaiq.strategist")


# ── Profile (input) ────────────────────────────────────────────────────────

@dataclass
class DocumentProfile:
    """Cheap pre-ingestion snapshot of a document's key characteristics.

    All fields are computable at upload time or from the first few pages
    without running the full ingestion pipeline.
    """
    # Always available at upload time
    mime: str                            # e.g. "application/pdf", "image/png"
    size_bytes: int                      # raw file size
    pages: int                           # from PyMuPDF page_count (cheap, <1s)

    # Sampled during the first pass (first 3 pages only)
    text_density: float | None = None    # chars / page_area — 0.0 = scanned, >0.05 = dense text
    has_text_layer: bool | None = None   # True if PyMuPDF extracted real text

    # Set after the classifier runs (may update strategy post-ingestion)
    doc_type: str | None = None          # e.g. "invoice", "contract", "passport"
    doc_type_confidence: float | None = None

    # File name hint (extension-free, for format detection edge cases)
    name_hint: str = ""


# ── Strategy (output) ──────────────────────────────────────────────────────

@dataclass
class ProcessingStrategy:
    """How the ingestion pipeline should handle this specific document.

    Every field has a sensible default (the current one-size-fits-all behaviour)
    so existing call sites work unchanged if they don't consume the strategy.
    """
    # ── embedding ──
    embed_backend: str = "local"         # "local" | "dashscope" | "openai"

    # ── chunking ──
    chunking_mode: str = "standard"      # "standard" | "layout_aware" | "section_based"

    # ── OCR ──
    ocr_mode: str = "auto"               # "auto" (detect text layer) | "vision" (force OCR) | "native" (skip OCR)

    # ── extraction ──
    extraction_mode: str = "full"        # "full" (all chunks) | "sampled" (stride-based) | "section_summary"

    # ── artifact generation ──
    artifact_tier: str = "full"          # "full" | "reduced" | "summary_only" | "skipped"

    # ── scheduling ──
    timeout_seconds: int = 300           # recommended worker job timeout
    priority: str = "normal"             # "normal" | "background" (large docs can queue)

    # ── metadata ──
    reason: str = ""                     # human-readable explanation of the chosen strategy

    # ── feature flags this strategy wants ──
    use_contextual_retrieval: bool = True
    use_table_extraction: bool = True
    use_pdfplumber_tables: bool = True

    def to_dict(self) -> dict:
        return {
            "embed_backend": self.embed_backend,
            "chunking_mode": self.chunking_mode,
            "ocr_mode": self.ocr_mode,
            "extraction_mode": self.extraction_mode,
            "artifact_tier": self.artifact_tier,
            "timeout_seconds": self.timeout_seconds,
            "priority": self.priority,
            "reason": self.reason,
        }


# ── Thresholds (tunable) ───────────────────────────────────────────────────

# Page-count bands
SMALL_MAX_PAGES = 30        # ≤30pp → full processing, local embedding viable
MEDIUM_MAX_PAGES = 100      # 31-100pp → dashscope embedding, layout-aware chunking
LARGE_MAX_PAGES = 300       # 101-300pp → section-based chunking, sampled extraction
# >300pp → summary-only, background priority

# Text density (chars / page_area in PDF units). A typical A4 page (595×842 pt)
# with ~3000 chars of text has density ~0.006. Calibrated on the real corpus:
#   0.01+  → dense native text (10+ KB text per page)
#   0.001-0.01 → sparse/mixed (a few lines, forms, image-heavy)
#   <0.001 → scanned/image-only (effectively no extractable text)
DENSE_TEXT_THRESHOLD = 0.01     # >0.01 → native text
SPARSE_TEXT_THRESHOLD = 0.001   # 0.001-0.01 → mixed / sparse
# <0.001 → scanned (needs vision OCR)

# Size thresholds
LARGE_FILE_BYTES = 50 * 1024 * 1024    # 50 MB — unusually large, flag for background

# Timeout bands (seconds)
TIMEOUT_SMALL = 300      # ≤30pp fits comfortably
TIMEOUT_MEDIUM = 600     # 31-100pp needs headroom for embedding
TIMEOUT_LARGE = 900      # 101-300pp — section-based chunking + dashscope
TIMEOUT_XLARGE = 1200    # 300+pp — summary-only, background queue


# ── Strategist ──────────────────────────────────────────────────────────────

def strategize(profile: DocumentProfile) -> ProcessingStrategy:
    """Pure function: profile → strategy.

    No I/O, no LLM, no DB. The matrix below encodes all the domain knowledge
    about which strategies work for which document profiles. Tune the thresholds
    above; the logic stays the same.
    """
    s = ProcessingStrategy()
    pages = profile.pages
    density = profile.text_density
    size_mb = profile.size_bytes / (1024 * 1024) if profile.size_bytes else 0

    # ── 1. OCR mode: does this document need vision OCR? ──────────────────
    if profile.has_text_layer is False or (density is not None and density < SPARSE_TEXT_THRESHOLD):
        s.ocr_mode = "vision"
    elif (density is not None and density < DENSE_TEXT_THRESHOLD
          and profile.has_text_layer is not True):
        # Only flag as mixed when we're unsure about the text layer.
        # A confirmed text layer with low density is just a sparse-text doc
        # (forms, certificates) — not mixed text/image.
        s.ocr_mode = "auto"
    else:
        s.ocr_mode = "native" if profile.has_text_layer else "auto"

    # ── 2. Embedding backend ─────────────────────────────────────────────
    # Local BGE-M3 is great for small docs but becomes the bottleneck for
    # large docs on CPU-only servers. DashScope API offloads embedding so
    # the pipeline stays fast regardless of chunk count.
    if pages <= SMALL_MAX_PAGES and s.ocr_mode != "vision":
        s.embed_backend = "local"       # BGE-M3 on CPU — fast for ≤30pp
    else:
        s.embed_backend = "dashscope"   # API-based — no CPU/RAM pressure

    # ── 3. Chunking mode ─────────────────────────────────────────────────
    if pages <= SMALL_MAX_PAGES:
        s.chunking_mode = "standard"
    elif pages <= MEDIUM_MAX_PAGES:
        s.chunking_mode = "layout_aware"
    else:
        s.chunking_mode = "section_based"

    # ── 4. Extraction mode ───────────────────────────────────────────────
    if pages <= SMALL_MAX_PAGES:
        s.extraction_mode = "full"
    elif pages <= MEDIUM_MAX_PAGES:
        s.extraction_mode = "full"       # still fits in LLM context with stride sampling
    elif pages <= LARGE_MAX_PAGES:
        s.extraction_mode = "sampled"    # stride-based sampling
    else:
        s.extraction_mode = "section_summary"  # summarise sections, extract from summaries

    # ── 5. Artifact tier (align with materialize_artifacts thresholds) ────
    # These match the existing _FULL/REDUCED/SUMMARY_ONLY/SKIPPED constants
    # in materialize_artifacts.py — kept in sync here so the strategist is
    # the single source of truth.
    if pages <= 30:
        s.artifact_tier = "full"
    elif pages <= 100:
        s.artifact_tier = "reduced"
    elif pages <= 300:
        s.artifact_tier = "summary_only"
    else:
        s.artifact_tier = "skipped"

    # ── 6. Timeout ───────────────────────────────────────────────────────
    if pages <= SMALL_MAX_PAGES:
        s.timeout_seconds = TIMEOUT_SMALL
    elif pages <= MEDIUM_MAX_PAGES:
        s.timeout_seconds = TIMEOUT_MEDIUM
    elif pages <= LARGE_MAX_PAGES:
        s.timeout_seconds = TIMEOUT_LARGE
    else:
        s.timeout_seconds = TIMEOUT_XLARGE

    # Vision OCR adds significant latency — bump timeout for scanned docs
    if s.ocr_mode == "vision" and s.timeout_seconds < TIMEOUT_LARGE:
        s.timeout_seconds = max(s.timeout_seconds, TIMEOUT_LARGE)

    # ── 7. Priority ──────────────────────────────────────────────────────
    if pages > LARGE_MAX_PAGES or size_mb > 50:
        s.priority = "background"
    else:
        s.priority = "normal"

    # ── 8. Feature flags ─────────────────────────────────────────────────
    # Disable expensive features for very large docs
    if pages > MEDIUM_MAX_PAGES:
        s.use_pdfplumber_tables = False    # pdfplumber is slow on large docs
    if pages > LARGE_MAX_PAGES:
        s.use_contextual_retrieval = False  # contextual retrieval needs full-doc LLM call
        s.use_table_extraction = False

    # ── 9. Format-specific overrides ─────────────────────────────────────
    mime_lower = profile.mime.lower()

    # Images always go through vision, regardless of page count
    if mime_lower.startswith("image/"):
        s.ocr_mode = "vision"
        s.embed_backend = "dashscope"   # image uploads are usually single-page; dashscope is fine
        s.chunking_mode = "standard"
        s.extraction_mode = "full"
        s.timeout_seconds = max(s.timeout_seconds, 300)

    # Spreadsheets: table-aware chunking always
    if "spreadsheet" in mime_lower or "excel" in mime_lower or profile.name_hint.endswith((".xlsx", ".xls", ".csv", ".tsv")):
        s.chunking_mode = "layout_aware"   # table-aware
        s.embed_backend = "dashscope"
        s.use_pdfplumber_tables = False

    # Plain text / EML — always small, local is fine
    if mime_lower.startswith("text/") or profile.name_hint.endswith((".txt", ".md", ".eml", ".log")):
        if pages <= SMALL_MAX_PAGES:
            s.embed_backend = "local"
        s.chunking_mode = "standard"

    # ── 10. Build the reason string ──────────────────────────────────────
    parts = []
    if s.ocr_mode == "vision":
        parts.append("scanned→vision OCR")
    elif s.ocr_mode == "auto":
        parts.append("mixed text/image")
    else:
        parts.append("native text")

    if pages <= SMALL_MAX_PAGES:
        parts.append(f"{pages}pp small")
    elif pages <= MEDIUM_MAX_PAGES:
        parts.append(f"{pages}pp medium")
    elif pages <= LARGE_MAX_PAGES:
        parts.append(f"{pages}pp large")
    else:
        parts.append(f"{pages}pp xlarge")

    parts.append(f"embed={s.embed_backend}")
    parts.append(f"chunk={s.chunking_mode}")
    parts.append(f"extract={s.extraction_mode}")
    parts.append(f"timeout={s.timeout_seconds}s")
    parts.append(f"priority={s.priority}")

    s.reason = " · ".join(parts)
    return s


def profile_from_upload(
    raw_bytes: bytes,
    mime: str,
    size_bytes: int,
    name: str = "",
) -> DocumentProfile:
    """Build a DocumentProfile from upload-time data with cheap sampling.

    Runs PyMuPDF page count + text density on the first 3 pages only.
    Designed to be called synchronously in the upload handler — takes <1s
    even for large files.
    """
    profile = DocumentProfile(
        mime=mime,
        size_bytes=size_bytes,
        pages=1,  # default, updated below
        name_hint=name,
    )

    mime_lower = mime.lower()

    # PDF: count pages + sample text density
    if "pdf" in mime_lower or name.lower().endswith(".pdf"):
        try:
            import fitz
            with fitz.open(stream=raw_bytes, filetype="pdf") as doc:
                profile.pages = int(doc.page_count)
                # Sample first 3 pages for text density
                sample_pages = min(3, profile.pages)
                total_chars = 0
                total_area = 0.0
                has_text = False
                for i in range(sample_pages):
                    page = doc[i]
                    text = page.get_text("text")
                    if text and len(text.strip()) > 40:
                        has_text = True
                    total_chars += len(text)
                    rect = page.rect
                    total_area += rect.width * rect.height
                if total_area > 0:
                    profile.text_density = total_chars / total_area
                profile.has_text_layer = has_text
        except Exception:
            log.warning("strategist: PDF sampling failed for %s — using defaults", name)
            profile.has_text_layer = True  # assume native text, let ingestion discover

    # Image: single page, needs vision
    elif mime_lower.startswith("image/"):
        profile.pages = 1
        profile.text_density = 0.0
        profile.has_text_layer = False

    # Office / text / other: assume single page (real count set during ingestion)
    else:
        profile.pages = 1
        profile.has_text_layer = True

    return profile
