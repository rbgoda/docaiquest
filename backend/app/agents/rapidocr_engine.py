"""RapidOCR engine · best-of-class word-level OCR for image documents.

M40 Phase F2 · the high-accuracy OCR backend for the per-field bbox
pipeline. Cascades with Tesseract via app/agents/ocr_router.py:

  RapidOCR (this module) → Tesseract (ocr.py) → no-bbox

Why RapidOCR
------------
RapidOCR is the ONNX-runtime port of the PaddleOCR detection + recognition
+ classification models. Compared to Tesseract:

  * Better on rotated / skewed IDs (phone photos)
  * Better on multilingual text (Chinese, Japanese, Korean, Indian
    languages) — IBM Docling uses RapidOCR internally for the same reason
  * Detection model returns 4-point polygons, not axis-aligned boxes —
    which means we get correct geometry on rotated text instead of a
    loose bounding box
  * ~60MB total (engine + 3 ONNX models bundled in the pip package)
  * No PyTorch dep — pure ONNX runtime, fast on CPU
  * MIT licensed

API mirrors `app/agents/ocr.py` exactly — same `extract_words(image_bytes)`
returning `(list[OcrWord], image_w, image_h)` — so the locator code in
ocr.py (token-search → bbox union) is reused without modification.

Lazy import so the rest of the worker still boots when rapidocr
isn't installed (tests, slim dev images). Caller treats `([], 0, 0)` as
"no OCR available" and the router falls through to Tesseract.

Engine singleton
----------------
RapidOCR loads ~60MB of ONNX models the first time it's instantiated.
We cache the engine at module level — subsequent calls reuse the loaded
session (~50ms per page after warm-up). The cache is per-worker process,
which matches Arq's model.
"""

from __future__ import annotations

import logging
from io import BytesIO
from typing import Any

# Reuse the dataclass + the field-locator from the Tesseract module so the
# downstream `locate_fields()` call works on RapidOCR words unchanged.
from app.agents.ocr import OcrWord

log = logging.getLogger("docaiq.agents.rapidocr")


_ENGINE_SINGLETON: Any = None


def _get_engine() -> Any:
    """Lazy-init the RapidOCR session. ~3-5s cold start (model load), then
    cheap. Returns None when the dep isn't installed — caller falls back."""
    global _ENGINE_SINGLETON
    if _ENGINE_SINGLETON is not None:
        return _ENGINE_SINGLETON
    try:
        from rapidocr import RapidOCR  # type: ignore
    except ImportError as e:
        log.info("rapidocr: not installed — fall back to Tesseract. err=%s", e)
        return None
    try:
        _ENGINE_SINGLETON = RapidOCR()
        log.info("rapidocr: engine loaded (models cached at ~/.rapidocr)")
        return _ENGINE_SINGLETON
    except Exception as e:  # noqa: BLE001
        log.warning("rapidocr: failed to construct engine: %s", e)
        return None


def extract_words(image_bytes: bytes) -> tuple[list[OcrWord], int, int]:
    """Run RapidOCR on the image, return word-level OcrWord rows.

    Returns ([], 0, 0) on any failure — the router treats this as "engine
    unavailable" and falls through to Tesseract. Caller never has to know
    which engine produced the output; the OcrWord shape is shared.

    The output coords are absolute pixel space on the source image, ready
    for `app.agents.ocr.locate_fields()`.
    """
    engine = _get_engine()
    if engine is None:
        return [], 0, 0
    try:
        from PIL import Image
        import numpy as np  # type: ignore
    except ImportError as e:
        log.warning("rapidocr: Pillow/numpy missing: %s", e)
        return [], 0, 0

    try:
        img = Image.open(BytesIO(image_bytes))
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        w, h = img.size
        arr = np.asarray(img)
        # RapidOCR call: result is a list of (polygon, text, confidence)
        # tuples per detected word/phrase. polygon is 4 points clockwise
        # from top-left. det/rec/cls all run in one call.
        result, _elapsed = engine(arr)
    except Exception as e:  # noqa: BLE001
        log.warning("rapidocr: inference failed: %s", e)
        return [], 0, 0

    if not result:
        return [], w, h

    words: list[OcrWord] = []
    for entry in result:
        # RapidOCR returns: [[polygon], text, confidence]  OR
        # in newer versions: (polygon, text, confidence). Handle both.
        try:
            polygon, text, conf = entry
        except (TypeError, ValueError):
            continue
        text = (text or "").strip()
        if not text:
            continue
        try:
            conf_f = float(conf)
        except (TypeError, ValueError):
            conf_f = 0.0
        # Polygon → axis-aligned bbox. RapidOCR's polygons are tight; the
        # AABB is a slight over-approximation but adequate for our use.
        try:
            xs = [float(p[0]) for p in polygon]
            ys = [float(p[1]) for p in polygon]
        except (TypeError, IndexError, ValueError):
            continue
        x0 = int(min(xs))
        y0 = int(min(ys))
        x1 = int(max(xs))
        y1 = int(max(ys))
        # Conf is 0-1 in RapidOCR. Normalize to 0-100 to match Tesseract
        # so the same downstream confidence threshold (≥ 20 in ocr.py)
        # applies cleanly across engines.
        words.append(OcrWord(text=text, x0=x0, y0=y0, x1=x1, y1=y1, conf=conf_f * 100))

    log.info("rapidocr: extracted %d words from %dx%d image", len(words), w, h)
    return words, w, h


def is_available() -> bool:
    """Cheap probe — does the dep import without error? Used by the router
    to decide which engine to try first without actually running OCR."""
    try:
        import rapidocr  # type: ignore  # noqa: F401
        return True
    except ImportError:
        return False
