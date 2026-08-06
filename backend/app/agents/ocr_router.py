"""OCR engine router · pick the best available per call.

M40 Phase F · single entry point for the per-field-bbox pipeline. Routes
between three engines in priority order:

  1. RapidOCR — best multilingual accuracy, transformer-based detection,
                rotated-text-friendly, 4-point polygons
  2. Tesseract — proven baseline, English-clean docs, axis-aligned boxes
  3. None     — return empty; FE legend still works without bboxes

Selection logic
---------------
* `DOCAIQ_OCR_ENGINE=rapidocr` → only RapidOCR, never fall through
* `DOCAIQ_OCR_ENGINE=tesseract` → only Tesseract
* `DOCAIQ_OCR_ENGINE=auto` (or unset, default) → try RapidOCR first; if
                                              that returns zero words or
                                              isn't installed, fall through
                                              to Tesseract

The caller (`kyc_extractor._augment_with_ocr_bboxes`) just calls
`extract_words(image_bytes)` and gets a (words, w, h) tuple — never has to
know which engine ran. The OcrWord shape is shared, so the downstream
locator (`ocr.locate_fields`) consumes either source uniformly.

Why a router instead of just calling the best engine directly
-------------------------------------------------------------
* Dev / test environments may not have all engines installed
* Different doc types may benefit from different engines (future-proofing
  for per-doc-type routing — e.g. Aadhaar prefers RapidOCR for Hindi)
* Cost / latency trade-offs may shift; env var lets ops override without
  a code change
"""

from __future__ import annotations

import logging
import os
from typing import Literal

from app.agents.ocr import OcrWord

log = logging.getLogger("docaiq.agents.ocr_router")

EngineName = Literal["rapidocr", "tesseract", "auto"]


def _selected_engine() -> EngineName:
    val = (os.environ.get("DOCAIQ_OCR_ENGINE") or "auto").lower().strip()
    if val in ("rapidocr", "tesseract", "auto"):
        return val  # type: ignore[return-value]
    log.warning("ocr_router: unknown DOCAIQ_OCR_ENGINE=%r, falling back to 'auto'", val)
    return "auto"


def extract_words(image_bytes: bytes) -> tuple[list[OcrWord], int, int]:
    """Run OCR via the configured engine, with cascade fallback under 'auto'.

    Returns (words, image_w, image_h). Empty words list when every engine
    fails — caller treats as "no bbox available" and falls back to the
    LLM-emitted box_2d (which may also be empty) or no doc-side rect.
    """
    engine = _selected_engine()
    from app.agents import rapidocr_engine, ocr as tesseract_engine

    # Explicit engine selection — no fallthrough.
    if engine == "rapidocr":
        return rapidocr_engine.extract_words(image_bytes)
    if engine == "tesseract":
        return tesseract_engine.extract_words(image_bytes)

    # Auto · try RapidOCR first. Fall through if:
    #   (a) the package isn't installed (lazy probe), or
    #   (b) inference returned zero words (likely model failure or
    #       unrecognized format)
    if rapidocr_engine.is_available():
        words, w, h = rapidocr_engine.extract_words(image_bytes)
        if words:
            log.debug("ocr_router: served by RapidOCR · %d words", len(words))
            return words, w, h
        log.info("ocr_router: RapidOCR returned 0 words, falling back to Tesseract")

    words, w, h = tesseract_engine.extract_words(image_bytes)
    log.debug("ocr_router: served by Tesseract · %d words", len(words))
    return words, w, h
