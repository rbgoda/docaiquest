"""Tesseract OCR · deterministic word-level bboxes for image documents.

Two-stage bbox architecture for image uploads (M40 Phase F · Tesseract):

  Stage 1 · LLM extracts typed field VALUES
    kyc_extractor (Gemini / Claude vision) → {holder_name: "GODA RAJESH",
                                              document_number: "MH02 20090183263", …}
    LLMs are excellent at semantic field identification but unreliable at
    pixel-precise positioning (Gemini box_2d misses or hallucinates, Claude
    bbox is best-effort prompt-following).

  Stage 2 · Tesseract locates each value on the image (this module)
    pytesseract image_to_data → word-level bboxes for every detectable
    glyph cluster on the image. We then text-search the OCR words for each
    extracted field value and union the matching word boxes into a single
    tight rect per field.

The output shape is the SAME `{x0, y0, x1, y1, page_w, page_h, page}` dict
the rest of the pipeline already understands (PyMuPDF, Gemini box_2d, the
frontend FieldOverlay) — page_w / page_h are the image's pixel dimensions
so the FE scales to its rendered width automatically.

Design notes
------------
* OCR is slow (1-3s per image on CPU) but runs in the worker, so it doesn't
  block the request path. Cached by document_pk; re-extract bypasses cache.
* Tesseract handles English well out of the box. For non-Latin scripts
  (Hindi on Aadhaar, Chinese on HKID, etc.) we'd add language packs via
  the Docker image. English-only is fine for the M40 ship.
* We use `image_to_data` (not `image_to_string`) because it returns
  per-word geometry that the bbox locator needs. Confidence is also
  returned per word — useful for filtering noise.
* PyMuPDF / Docling could replace this for PDFs and complex layouts. For
  now this is the SIMPLEST thing that solves the image-bbox problem.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from io import BytesIO
from typing import Any

log = logging.getLogger("docaiq.agents.ocr")


@dataclass(frozen=True)
class OcrWord:
    """One OCR-detected word with its pixel bbox.

    `text` is the recognized glyph cluster (already stripped of whitespace
    by Tesseract). `x0, y0, x1, y1` are absolute pixel coordinates on the
    source image. `conf` is Tesseract's confidence 0-100 (we'll typically
    keep words above ~30; below that is line noise).
    """

    text: str
    x0: int
    y0: int
    x1: int
    y1: int
    conf: float


def extract_words(image_bytes: bytes, lang: str = "eng") -> tuple[list[OcrWord], int, int]:
    """Run Tesseract on an image and return (words, image_width, image_height).

    Returns ([], 0, 0) on any failure — caller treats that as "no OCR
    available" and the FE legend still works (just without per-field
    rectangles). pytesseract import is lazy so the module can be imported
    in environments without tesseract installed (tests, local dev without
    the Docker apt package).
    """
    try:
        import pytesseract
        from PIL import Image
    except ImportError as e:
        log.warning("ocr: pytesseract/Pillow not available — install with apt-get install tesseract-ocr + pip install pytesseract Pillow. err=%s", e)
        return [], 0, 0

    try:
        img = Image.open(BytesIO(image_bytes))
        # Some IDs come through as RGBA or palette; Tesseract prefers RGB / L.
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        w, h = img.size
        data = pytesseract.image_to_data(
            img, lang=lang, output_type=pytesseract.Output.DICT
        )
    except Exception as e:  # noqa: BLE001
        log.warning("ocr: tesseract run failed: %s", e)
        return [], 0, 0

    n = len(data.get("text", []))
    words: list[OcrWord] = []
    for i in range(n):
        text = (data["text"][i] or "").strip()
        if not text:
            continue
        try:
            conf = float(data["conf"][i])
        except (TypeError, ValueError):
            conf = 0.0
        # Drop garbage. -1 is Tesseract's "no confidence" marker; <20 is
        # almost always misread strokes / scan artifacts.
        if conf < 20:
            continue
        x = int(data["left"][i])
        y = int(data["top"][i])
        w_ = int(data["width"][i])
        h_ = int(data["height"][i])
        words.append(OcrWord(text=text, x0=x, y0=y, x1=x + w_, y1=y + h_, conf=conf))

    log.info("ocr: extracted %d words from %dx%d image", len(words), w, h)
    return words, w, h


# ── Field-value → bbox locator ────────────────────────────────────────────


# Token normalization rules. The LLM emits "GODA RAJESH" but Tesseract may
# read "Goda" + "Rajesh" or even "G0DA" (zero/O confusion) — so we lowercase
# and strip punctuation before matching.
_PUNCT_RE = re.compile(r"[^\w\d]+", re.UNICODE)


def _normalize(s: str) -> str:
    """Lowercase + strip punctuation. Used on both LLM values and OCR words
    so a search for 'mh02-20090183263' matches Tesseract's 'MH02' + '20090183263'."""
    return _PUNCT_RE.sub("", (s or "").lower())


def _tokenize_value(value: str) -> list[str]:
    """Split a field value into normalized tokens for sequential matching.
    Whitespace and punctuation both split, so '1968-10-10' → ['1968', '10', '10']
    and 'THE FOX SNACKS' → ['the', 'fox', 'snacks']. Empty tokens dropped."""
    if not value:
        return []
    return [t for t in _PUNCT_RE.split(value.lower()) if t]


def locate_fields(
    words: list[OcrWord],
    fields: dict[str, Any],
    image_w: int,
    image_h: int,
) -> dict[str, dict[str, Any]]:
    """For each extracted typed field, find the OCR word(s) that spell out
    its value and emit a unioned bbox.

    Two complementary strategies are tried per field (first hit wins):

      A. Single-OCR-word match — the entire field value, punctuation-stripped
         and lowercased, equals one OCR word's normalized text. Handles
         glued-together values that Tesseract returns as a single token:
            "1987-01-16"     → OCR word "1987-01-16"      ✓
            "MH02-20090183263" → OCR word "MH0220090183263" ✓
            "S1234567A"      → OCR word "S1234567A"       ✓

      B. Multi-OCR-word run match — the value's tokens (split on whitespace
         AND punctuation) match a consecutive run of OCR words in order.
         Handles values that Tesseract splits across multiple word boxes:
            "GODA RAJESH"     → OCR words ["GODA", "RAJESH"]      ✓
            "THE FOX SNACKS"  → OCR words ["THE", "FOX", "SNACKS"] ✓

    Strategy B has a 1-token tolerance for phrases of 3+ words and a
    distinctive-prefix fallback for 2-word phrases — see _find_token_run.

    Returns: {field_name: {x0,y0,x1,y1,page_w,page_h,page}} ready to write
    into Document.extracted_fields.field_bboxes. Skips non-scalar values
    (arrays, objects), empty strings, and private `_*` keys.
    """
    if not words or not fields or image_w <= 0 or image_h <= 0:
        return {}

    # Pre-compute normalized OCR words once.
    # `ocr_normed_joined[i]` strips punctuation → matches a value like
    # "1987-01-16" against an OCR word "1987-01-16" → "19870116".
    ocr_normed_joined = [_normalize(w.text) for w in words]

    bboxes: dict[str, dict[str, Any]] = {}
    for fname, value in fields.items():
        if fname.startswith("_"):
            continue
        if not isinstance(value, (str, int, float)):
            continue
        value_str = str(value).strip()
        if not value_str:
            continue

        # Strategy A · single-OCR-word match via joined normalization.
        # Cheap: O(N) scan, often hits for IDs / dates / codes.
        joined = _normalize(value_str)
        if joined and len(joined) >= 3:
            for i, ocr_n in enumerate(ocr_normed_joined):
                if ocr_n == joined:
                    bboxes[fname] = _bbox_dict(words[i:i + 1], image_w, image_h)
                    break
            if fname in bboxes:
                continue

        # Strategy B · multi-OCR-word run match.
        tokens = _tokenize_value(value_str)
        if not tokens:
            continue
        match = _find_token_run(ocr_normed_joined, tokens)
        if match is None:
            continue
        start, end = match
        bboxes[fname] = _bbox_dict(words[start:end], image_w, image_h)

    log.info("ocr: located %d / %d field bboxes via word-search", len(bboxes), len(fields))
    return bboxes


def _bbox_dict(matched: list[OcrWord], image_w: int, image_h: int) -> dict[str, Any]:
    """Union a list of OCR-word bboxes into our standard dict shape."""
    x0 = min(w.x0 for w in matched)
    y0 = min(w.y0 for w in matched)
    x1 = max(w.x1 for w in matched)
    y1 = max(w.y1 for w in matched)
    return {
        "x0": x0, "y0": y0, "x1": x1, "y1": y1,
        "page_w": image_w, "page_h": image_h, "page": 1,
    }


def _find_token_run(ocr_words: list[str], tokens: list[str]) -> tuple[int, int] | None:
    """Find the first consecutive run of OCR words whose normalized text
    matches the value's token sequence. Returns (start, end) for slicing
    `words[start:end]`, or None if no match.

    Tolerates one OCR-side token mismatch in the middle of the run when
    the value has 3+ tokens — handles cases where Tesseract garbles one
    word in a long phrase (common with mixed numerics).
    """
    if not tokens or not ocr_words:
        return None
    n_tok = len(tokens)
    n_words = len(ocr_words)
    if n_words < n_tok:
        return None
    # Strict pass — every token matches in order.
    for i in range(n_words - n_tok + 1):
        if all(ocr_words[i + j] == tokens[j] for j in range(n_tok)):
            return (i, i + n_tok)
    # Tolerance pass — allow one off-token for long phrases (≥3 tokens).
    if n_tok >= 3:
        for i in range(n_words - n_tok + 1):
            mismatches = sum(1 for j in range(n_tok) if ocr_words[i + j] != tokens[j])
            if mismatches <= 1:
                return (i, i + n_tok)
    # Partial-prefix fallback — match the first 2 tokens, accept whatever
    # span those cover (good enough for "GODA RAJESH" when "RAJESH" was
    # mis-OCR'd as "RAJESHI").
    if n_tok >= 2:
        for i in range(n_words - 1):
            if ocr_words[i] == tokens[0] and ocr_words[i + 1] == tokens[1]:
                return (i, i + 2)
    # Single-token fallback — for distinctive long values (e.g. document
    # numbers ≥ 8 chars), one-word match is fine.
    if n_tok == 1 and len(tokens[0]) >= 8:
        for i, w in enumerate(ocr_words):
            if w == tokens[0]:
                return (i, i + 1)
    return None
