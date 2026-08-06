"""Vision-OCR ingestion path.

Used for documents that have NO extractable text via PyMuPDF:
  - Uploads in image MIME types (image/jpeg, image/png, image/webp, image/gif)
  - PDFs whose pages are scanned images (no text layer)

The image bytes (or each rasterised PDF page) are sent to Anthropic vision
via OpenRouter. The model returns a structured transcript per page — clean
text with linebreaks preserved where layout matters. That transcript then
flows through the normal `chunk_pages → embed → bbox` path in
`ingestion.py`, so downstream code (retrieval, fact extractor, chat) sees
chunks the same shape as PDF-derived ones.

This is the OCR sibling of the PDF parser — same output type
(`list[tuple[page_no, text]]`), different parser. Picking it up at the
parser stage means the rest of the pipeline is untouched.

Why Anthropic vision (claude-haiku-4.5 via OpenRouter)
-----------------------------------------------------
- Better at structured ID-card / passport extraction than tesseract or
  Google Document AI for our doc set
- One LLM call covers both OCR and rough layout — paragraphs/lines/tables
- Reuses the existing OpenRouter key
- ~$0.003/page input + ~$0.001/output → ~$4 per 1,000 pages

HEIC / WebP
-----------
HEIC and AVIF aren't directly supported by OpenAI's image format spec.
We handle this with Pillow's heif plugin (`pillow-heif`) to transcode to
JPEG before sending. WebP and PNG go through as-is.
"""

from __future__ import annotations

import base64
import io
import logging
import re
import time as _time

import httpx
from sqlalchemy.orm import Session

from app.config import get_settings
from app.feature_flags import is_enabled, get_float, get_int, get_str
from app.llm import ledger
from app.llm.prompts import get_prompt

log = logging.getLogger(__name__)


_CLAUDE_MODEL = "anthropic/claude-haiku-4.5"
_URL = "https://openrouter.ai/api/v1/chat/completions"
_VISION_TIMEOUT = 90.0   # vision calls can be slow on multi-page docs
_MAX_PAGES_PER_DOC = 50  # legacy cap — overridden by config.documents_max_ocr_pages when set  # T3.2 · raised from 25 (was silently cutting off legitimate compliance docs)
# Vision OCR is output-heavy. Rough blended rate; proper input/output split is TODO #11.
_VISION_RATE_USD_PER_MTOK = 3.0


_VISION_PROMPT = get_prompt("vision_transcribe")


def _normalise_mime(mime: str) -> str:
    """Map MIME variants to the canonical form OpenRouter accepts."""
    m = (mime or "").lower().strip()
    if m == "image/jpg":
        return "image/jpeg"
    return m


def _transcode_to_jpeg(raw: bytes, mime: str) -> tuple[bytes, str]:
    """Convert HEIC/HEIF (or anything Pillow can read) to JPEG. Returns the
    new bytes + MIME. Falls back to the original on any conversion error so
    the caller can still attempt the OpenRouter request (it'll fail with a
    clearer message there if the format really isn't supported)."""
    try:
        from PIL import Image  # type: ignore

        # pillow-heif provides HEIF/HEIC decode via Pillow registration.
        try:
            import pillow_heif  # type: ignore

            pillow_heif.register_heif_opener()
        except Exception:  # noqa: BLE001
            pass

        img = Image.open(io.BytesIO(raw))
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=88)
        return out.getvalue(), "image/jpeg"
    except Exception as e:  # noqa: BLE001
        log.warning("vision: image transcode (%s → jpeg) failed: %s", mime, e)
        return raw, mime


def _autorotate(img, *, osd_enabled: bool, osd_min_conf: float = 3.0):
    """G6 · correct image orientation. Returns (img, changed).

    1. EXIF orientation — ALWAYS applied; safe + deterministic (fixes phone
       photos that record rotation in metadata; a no-op without the tag).
    2. Tesseract OSD — OPT-IN only (`osd_enabled`). OSD can mis-detect an
       already-upright page and wrongly rotate it, so it fires only on a very
       high orientation confidence and never by default. Any failure → no-op.
    """
    from PIL import ImageOps
    changed = False
    try:
        orient = img.getexif().get(0x0112)
        if orient and int(orient) != 1:
            img = ImageOps.exif_transpose(img)
            changed = True
    except Exception:  # noqa: BLE001
        pass
    if osd_enabled:
        try:
            import pytesseract  # type: ignore
            osd = pytesseract.image_to_osd(
                img, config="--dpi 150", output_type=pytesseract.Output.DICT
            )
            rot = int(osd.get("rotate", 0) or 0)
            conf = float(osd.get("orientation_conf", 0) or 0)
            if rot in (90, 180, 270) and conf >= osd_min_conf:
                img = img.rotate(rot, expand=True)  # PIL CCW · matches OSD value
                changed = True
        except Exception:  # noqa: BLE001
            pass
    return img, changed


def prepare_image_for_vision(
    raw: bytes, mime: str, *, max_edge: int = 1568, max_bytes: int = 4_500_000,
) -> tuple[bytes, str]:
    """Downscale + re-encode an image so it fits provider vision limits.

    Anthropic (incl. via OpenRouter) rejects images over ~5 MB or with a very
    long edge with a 400 'Provider returned error'. A 6 MB phone photo or
    hi-res screenshot then fails classification + vision-extract silently. We
    cap the longest edge at `max_edge` and re-encode to JPEG, dropping quality
    until under `max_bytes`. Falls back to the original on any error.
    """
    try:
        from PIL import Image
        try:
            import pillow_heif  # type: ignore
            pillow_heif.register_heif_opener()
        except Exception:  # noqa: BLE001
            pass
        img = Image.open(io.BytesIO(raw))
        if img.mode in ("RGBA", "P", "LA"):
            img = img.convert("RGB")
        # G6 · orientation correction (EXIF always; OSD opt-in via settings).
        from app.config import get_settings as _gs
        _s = _gs()
        img, rotated = _autorotate(
            img, osd_enabled=is_enabled("ocr_osd_autorotate", False),
            osd_min_conf=get_float("ocr_osd_min_conf", 3.0)
        )
        w, h = img.size
        longest = max(w, h)
        small_enough = len(raw) <= max_bytes and mime in _SUPPORTED_IMAGE_MIMES
        if not rotated and longest <= max_edge and small_enough:
            return raw, mime  # already within limits + correctly oriented
        if longest > max_edge:
            scale = max_edge / float(longest)
            img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))))
        q = 85
        while True:
            out = io.BytesIO()
            img.save(out, format="JPEG", quality=q)
            data = out.getvalue()
            if len(data) <= max_bytes or q <= 45:
                return data, "image/jpeg"
            q -= 15
    except Exception as e:  # noqa: BLE001
        log.warning("prepare_image_for_vision failed (%s); sending original", e)
        return raw, mime


def _image_data_url(image_bytes: bytes, mime: str) -> str:
    b64 = base64.b64encode(image_bytes).decode("ascii")
    return f"data:{mime};base64,{b64}"


# ── Vision cascade · FREE Gemini → CHEAP Qwen3-VL (OpenRouter) → PAID Claude-Haiku ─
# Cost-aware: try the free tier first; escalate to paid only on failure/quota.
# A circuit-breaker stops us hammering Gemini once it's clearly rate-limited.
# Each tier's output passes through a heuristic quality gate (_critique_vision_output)
# before acceptance — garbage/refusals/gibberish trigger escalation.
# Gemini free-tier circuit-breaker (process-local, best-effort).
_GEMINI_FAIL_THRESHOLD = 3           # consecutive failures before tripping
_GEMINI_COOLDOWN_S = 120.0           # skip Gemini for this long once tripped
_gemini_fails = 0
_gemini_skip_until = 0.0
# Quality gate constants
_CRITIQUE_MIN_WORDS = 10             # fewer words = likely garbage/refusal
_CRITIQUE_MIN_CHARS = 30             # shorter body = not meaningful
_CRITIQUE_COHERENCE_FLOOR = 0.75     # below this ratio = garbled/unreadable


def _critique_vision_output(text: str) -> tuple[bool, float, str]:
    """Heuristic quality gate for vision output. Returns (pass, score 0–1, reason).

    Catches common LLM failures — empty/short output, garbled characters,
    refusal messages, repetitive hallucination — without an extra LLM call.
    Runs on every tier's output before accepting it, so garbage from the free
    tier automatically escalates to paid.
    """
    if not text or not text.strip():
        return False, 0.0, "empty"
    stripped = text.strip()

    # ── 1. Too short to contain meaningful content ──
    if len(stripped) < _CRITIQUE_MIN_CHARS:
        return False, 0.1, "too_short"

    # ── 2. Refusal / error / "I'm sorry" patterns from LLM guardrails ──
    lower = stripped.lower()
    refusal_markers = (
        "i'm sorry", "i cannot", "i can't", "unable to", "error:",
        "please provide", "i am not able", "as an ai", "i apologize",
        "sorry,", "i'm unable",
    )
    head = lower[:150]
    for marker in refusal_markers:
        if head.startswith(marker) or marker in head:
            return False, 0.05, f"refusal:{marker}"

    # ── 3. Word count check ──
    words = stripped.split()
    if len(words) < _CRITIQUE_MIN_WORDS:
        return False, 0.2, "too_few_words"

    # ── 4. Garbled text — ratio of readable characters ──
    meaningful = sum(
        1 for c in stripped
        if c.isalnum() or c.isspace()
        or c in '.,;:!?()[]{}#*|-–—_\'"\n\t />&%$@+=~₹€£¥'
    )
    coherence = meaningful / max(len(stripped), 1)
    if coherence < _CRITIQUE_COHERENCE_FLOOR:
        return False, coherence, f"garbled:{coherence:.2f}"

    # ── 5. Repetition check — same line repeated (hallucination loop) ──
    lines = [l.strip() for l in stripped.split("\n") if l.strip()]
    if len(lines) >= 5:
        from collections import Counter
        line_counts = Counter(lines)
        most_common_ratio = line_counts.most_common(1)[0][1] / len(lines)
        if most_common_ratio > 0.6:
            return False, 0.3, f"repetitive:{most_common_ratio:.2f}"

    # ── Composite score ──
    length_score = min(len(words) / 200, 1.0)
    score = 0.5 * coherence + 0.5 * length_score
    return True, score, "ok"


def _gemini_circuit_open() -> bool:
    """True when Gemini has been tripped and we should skip straight to paid."""
    return _time.monotonic() < _gemini_skip_until


def _gemini_record(ok: bool) -> None:
    global _gemini_fails, _gemini_skip_until
    if ok:
        _gemini_fails = 0
        _gemini_skip_until = 0.0
    else:
        _gemini_fails += 1
        if _gemini_fails >= _GEMINI_FAIL_THRESHOLD:
            _gemini_skip_until = _time.monotonic() + _GEMINI_COOLDOWN_S
            log.warning("vision: Gemini circuit OPEN for %ds (free-tier quota/errors); "
                        "routing to paid Qwen-VL", int(_GEMINI_COOLDOWN_S))
            _gemini_fails = 0


def _qwen_vision_raw(
    image_bytes: bytes, mime: str, prompt: str, *,
    max_tokens: int = 4000, task: str = "vision",
    db: Session | None = None, tenant_id: str | None = None, settings=None,
) -> str:
    """One Qwen-VL call via the paid Dashscope key (OpenAI-compatible mode).
    Returns raw text, or "" on failure. Shared by OCR transcription and the
    JSON vision-extract path. Model from settings.vision_qwen_model."""
    if settings is None:
        settings = get_settings()
    if not settings.dashscope_api_key:
        return ""
    model = settings.vision_qwen_model
    body = {
        "model": model,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": _image_data_url(image_bytes, mime)}},
            ],
        }],
        "max_tokens": max_tokens,
    }
    headers = {
        "Authorization": f"Bearer {settings.dashscope_api_key}",
        "Content-Type": "application/json",
    }
    url = f"{settings.dashscope_base_url.rstrip('/')}/chat/completions"
    t0 = _time.perf_counter()
    try:
        resp = httpx.post(url, json=body, headers=headers, timeout=_VISION_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        text = data["choices"][0]["message"]["content"] or ""
        if db is not None:
            usage = data.get("usage") or {}
            ledger.record_call(
                db, task=task, tier="t2", provider="dashscope", model=model,
                input_tokens=int(usage.get("prompt_tokens", 0)),
                output_tokens=int(usage.get("completion_tokens", 0)),
                cost_per_input_mtok=0.8, cost_per_output_mtok=3.2,
                latency_ms=int((_time.perf_counter() - t0) * 1000),
                status="ok", tenant_id=tenant_id,
            )
        return text.strip()
    except (httpx.HTTPError, KeyError, IndexError) as e:
        log.warning("vision (Qwen/Dashscope · %s): call failed: %s", task, e)
        if db is not None:
            ledger.record_call(
                db, task=task, tier="t2", provider="dashscope", model=model,
                status="failed", error=str(e),
                latency_ms=int((_time.perf_counter() - t0) * 1000), tenant_id=tenant_id,
            )
        return ""


def _vision_transcribe_qwen(
    image_bytes: bytes, mime: str,
    *, db: Session | None = None, tenant_id: str | None = None, settings=None,
    prompt: str = _VISION_PROMPT,
) -> str:
    """Qwen-VL OCR (paid Dashscope) — the escalation tier when free Gemini
    fails or its circuit is open."""
    return _qwen_vision_raw(image_bytes, mime, prompt, max_tokens=4000,
                            task="vision", db=db, tenant_id=tenant_id, settings=settings)


def _vision_transcribe_gemini(
    image_bytes: bytes, mime: str,
    *, db: Session | None = None, tenant_id: str | None = None, settings=None,
    prompt: str = _VISION_PROMPT,
) -> str:
    """Gemini Vision direct OCR via gemini-flash-latest (auto-resolves to newest Flash).
    Used as the preferred path when DOCAIQ_GOOGLE_GENAI_API_KEY is set."""
    if settings is None:
        settings = get_settings()
    model = "gemini-2.5-flash"
    body = {
        "contents": [{
            "role": "user",
            "parts": [
                {"text": prompt},
                {"inlineData": {"mimeType": mime, "data": base64.b64encode(image_bytes).decode()}},
            ],
        }],
        "generationConfig": {"temperature": 0.0, "maxOutputTokens": 8000},
    }
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent?key={settings.google_genai_api_key}"
    )
    t0 = _time.perf_counter()
    try:
        resp = httpx.post(url, json=body, timeout=_VISION_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        text = "".join(p.get("text", "") for p in (data["candidates"][0]["content"]["parts"]))
        if db is not None:
            meta = data.get("usageMetadata") or {}
            ledger.record_call(
                db, task="vision", tier="t2", provider="google", model=model,
                input_tokens=int(meta.get("promptTokenCount", 0)),
                output_tokens=int(meta.get("candidatesTokenCount", 0)),
                cost_per_input_mtok=0.15, cost_per_output_mtok=0.60,  # gemini-2.5-flash paid tier
                latency_ms=int((_time.perf_counter() - t0) * 1000),
                status="ok", tenant_id=tenant_id,
            )
        _gemini_record(True)
        return (text or "").strip()
    except (httpx.HTTPError, KeyError, IndexError) as e:
        log.warning("vision (Gemini): OCR call failed: %s", e)
        _gemini_record(False)
        if db is not None:
            ledger.record_call(
                db, task="vision", tier="t2", provider="google", model=model,
                status="failed", error=str(e),
                latency_ms=int((_time.perf_counter() - t0) * 1000),
                tenant_id=tenant_id,
            )
        return ""


def _vision_transcribe_one(
    image_bytes: bytes, mime: str,
    *, db: Session | None = None, tenant_id: str | None = None,
    prompt: str = _VISION_PROMPT,
) -> str:
    """Single vision call. Returns the transcript text, or empty string on
    failure (caller decides what to do with that). When `db` is provided,
    records the call in the LLM ledger so dashboard spend is accurate.

    `prompt` selects the transcription style: the default flat-text OCR prompt,
    or the structured-Markdown prompt for the Phase-2 Document-Model path.

    M31.8 · Prefer Gemini Vision direct when DOCAIQ_GOOGLE_GENAI_API_KEY
    is set — same quality as Anthropic-via-OpenRouter on KYC docs in our
    tests + higher RPM ceiling. Falls back to OpenRouter when Gemini key
    is missing.
    """
    settings = get_settings()
    # Cost-aware cascade with agentic critique. Each tier's output passes through a
    # heuristic quality gate (_critique_vision_output) before acceptance. Garbage /
    # refusals / gibberish from one tier automatically trigger escalation to the next.
    # Only output that looks like real content is accepted — no silent degradation.
    #
    # Cascade order is controlled by the `vision_primary` feature flag:
    #   "gemini" (default) — FREE Gemini first → PAID Qwen-VL fallback
    #   "qwen"            — PAID Qwen-VL first → FREE Gemini fallback
    if tenant_id:
        from app.db import current_tenant, get_current_tenant as _get_tid
        try:
            _get_tid()
        except Exception:
            current_tenant.set(tenant_id)
    from app.feature_flags import get_str
    primary = get_str("vision_primary", "gemini")
    qwen_first = (primary == "qwen")

    def _try_gemini():
        if not settings.google_genai_api_key or _gemini_circuit_open():
            return ""
        text = _vision_transcribe_gemini(
            image_bytes, mime, db=db, tenant_id=tenant_id, settings=settings, prompt=prompt,
        )
        if text:
            passed, score, reason = _critique_vision_output(text)
            if passed:
                return text
            log.warning("vision: Gemini output failed critique (score=%.2f, %s)", score, reason)
        else:
            log.info("vision: Gemini empty/failed")
        return ""

    def _try_qwen():
        if not settings.dashscope_api_key:
            return ""
        text = _vision_transcribe_qwen(
            image_bytes, mime, db=db, tenant_id=tenant_id, settings=settings, prompt=prompt,
        )
        if text:
            passed, score, reason = _critique_vision_output(text)
            if passed:
                return text
            log.warning("vision: Qwen-VL output failed critique (score=%.2f, %s)", score, reason)
        else:
            log.warning("vision: Qwen-VL empty/failed")
        return ""

    if qwen_first:
        # PAID Qwen-VL first → FREE Gemini fallback
        text = _try_qwen()
        if text:
            return text
        log.info("vision: Qwen-VL failed → falling back to Gemini")
        text = _try_gemini()
        if text:
            return text
        log.warning("vision: ALL fallbacks exhausted (qwen→gemini) — page OCR returned empty")
    else:
        # FREE Gemini first → PAID Qwen-VL fallback (default)
        text = _try_gemini()
        if text:
            return text
        log.info("vision: Gemini empty/failed → escalating to Qwen-VL (DashScope)")
        text = _try_qwen()
        if text:
            return text
        log.warning("vision: ALL fallbacks exhausted (gemini→qwen) — page OCR returned empty")
    return ""


def _vision_transcribe_openrouter(
    image_bytes: bytes, mime: str, model: str,
    *, db=None, tenant_id=None, settings=None,
    prompt: str = _VISION_PROMPT,
    cost_in: float = 0.15, cost_out: float = 0.60,
) -> str:
    """Vision OCR via OpenRouter with arbitrary model. Transparent per-token billing."""
    settings = settings or get_settings()
    if not settings.openrouter_api_key:
        return ""
    body = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": _image_data_url(image_bytes, mime)}},
                ],
            }
        ],
        "max_tokens": 4000,
    }
    headers = {
        "Authorization": f"Bearer {settings.openrouter_api_key}",
        "HTTP-Referer": settings.openrouter_referer,
        "X-Title": settings.openrouter_app_title,
        "Content-Type": "application/json",
    }
    t0 = _time.perf_counter()
    try:
        resp = httpx.post(_URL, json=body, headers=headers, timeout=_VISION_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        text = data["choices"][0]["message"]["content"] or ""
        if db is not None:
            usage = data.get("usage") or {}
            ledger.record_call(
                db, task="vision", tier="t2", provider="openrouter", model=model,
                input_tokens=int(usage.get("prompt_tokens", 0)),
                output_tokens=int(usage.get("completion_tokens", 0)),
                cost_per_input_mtok=cost_in, cost_per_output_mtok=cost_out,
                latency_ms=int((_time.perf_counter() - t0) * 1000),
                status="ok", tenant_id=tenant_id,
            )
        return text.strip()
    except (httpx.HTTPError, KeyError, IndexError) as e:
        log.warning("vision (OpenRouter/%s): OCR call failed: %s", model, e)
        if db is not None:
            ledger.record_call(
                db, task="vision", tier="t2", provider="openrouter", model=model,
                status="failed", error=str(e),
                latency_ms=int((_time.perf_counter() - t0) * 1000),
                tenant_id=tenant_id,
            )
        return ""


def _vision_transcribe_claude(
    image_bytes: bytes, mime: str,
    *, db: Session | None = None, tenant_id: str | None = None, settings=None,
    prompt: str = _VISION_PROMPT,
) -> str:
    """Claude-Haiku vision OCR via OpenRouter. Extracted as a standalone engine so
    G11 multi-pass can call it as a DISTINCT 2nd opinion (different model from the
    Qwen/Gemini primary). Returns '' on failure or no key."""
    return _vision_transcribe_openrouter(
        image_bytes, mime, _CLAUDE_MODEL,
        db=db, tenant_id=tenant_id, settings=settings, prompt=prompt,
        cost_in=1.0, cost_out=5.0,
    )


_SUPPORTED_IMAGE_MIMES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
_TRANSCODE_IMAGE_MIMES = {"image/heic", "image/heif", "image/avif"}


def parse_image(
    image_bytes: bytes, mime: str,
    *, db: Session | None = None, tenant_id: str | None = None,
    structured: bool = False,
) -> list[tuple[int, str]]:
    """Vision OCR an image as a single 'page 1'. Same return shape as
    ingestion.parse_pdf() so downstream code is identical. When `structured`,
    transcribes to GFM Markdown (Phase-2 Document-Model path)."""
    mime = _normalise_mime(mime)
    if mime in _TRANSCODE_IMAGE_MIMES:
        image_bytes, mime = _transcode_to_jpeg(image_bytes, mime)
    if mime not in _SUPPORTED_IMAGE_MIMES:
        log.warning("vision: MIME %s not directly supported; attempting transcode", mime)
        image_bytes, mime = _transcode_to_jpeg(image_bytes, mime)
        if mime not in _SUPPORTED_IMAGE_MIMES:
            return []
    # Cap size/dimensions so large uploads don't 400 the provider.
    image_bytes, mime = prepare_image_for_vision(image_bytes, mime)
    text = (transcribe_page_markdown(image_bytes, mime, db=db, tenant_id=tenant_id)
            if structured else
            _vision_transcribe_one(image_bytes, mime, db=db, tenant_id=tenant_id))
    if not text:
        return []
    return [(1, text)]


# ── Embedded-image OCR for Office files (docx/pptx) ────────────────────────
# Word/PowerPoint can carry their substance inside an embedded image — a pasted
# screenshot, a photo, a chart. The docx/pptx text parsers only see paragraphs,
# table cells and notes, so that content is otherwise dropped. These two helpers
# let the Office parsers vision-OCR those images (opt-in, cost-capped).

_OFFICE_IMAGE_MIN_EDGE = 200  # px; below this an embedded image is chrome
                              # (logo / icon / bullet), not content — skip it.


def embedded_image_is_content(image_bytes: bytes) -> bool:
    """Cheap, no-VLM check: is this blob a raster image big enough to be real
    content (both edges >= _OFFICE_IMAGE_MIN_EDGE) rather than a logo/icon?
    Callers gate on this BEFORE ocr_embedded_image so decorative chrome never
    costs a vision call. `Image.open(...).size` reads only the header (no full
    decode), so this is fast. Unreadable/vector (EMF/WMF) blobs → False."""
    try:
        from PIL import Image
        with Image.open(io.BytesIO(image_bytes)) as im:
            w, h = im.size
    except Exception:  # noqa: BLE001 — corrupt / non-raster / unsupported
        return False
    return min(w, h) >= _OFFICE_IMAGE_MIN_EDGE


def ocr_embedded_image(
    image_bytes: bytes, content_type: str | None = None,
    *, db: Session | None = None, tenant_id: str | None = None,
) -> str:
    """Vision-OCR one image extracted from an Office file. Returns the transcribed
    text, or '' on an empty/unreadable blob or an empty transcription. Callers
    should pre-filter with embedded_image_is_content() so this only runs on real
    content. prepare_image_for_vision transcodes anything Pillow can read to JPEG
    (and downscales to provider limits); a blob it can't read comes back with its
    original unsupported MIME and we bail to ''. Honours multi-pass OCR."""
    if not image_bytes:
        return ""
    # Confirm it's a decodable raster first. prepare_image_for_vision swallows a
    # decode failure and hands back the ORIGINAL bytes+mime, so a corrupt blob
    # carrying a supported MIME (e.g. "image/png") would otherwise reach the VLM.
    try:
        from PIL import Image
        with Image.open(io.BytesIO(image_bytes)) as _im:
            _im.load()
    except Exception:  # noqa: BLE001 — corrupt / non-raster / vector blob
        return ""
    image_bytes, mime = prepare_image_for_vision(image_bytes, content_type or "image/jpeg")
    if mime not in _SUPPORTED_IMAGE_MIMES:
        return ""
    return transcribe_page(image_bytes, mime, db=db, tenant_id=tenant_id) or ""


# ── P9.4 · vision-aware extraction ────────────────────────────────────────
# Beyond OCR-to-text: send the page IMAGE to the vision model and ask for
# layout-level signals that flat text loses — signature presence, stamps/
# seals, checkbox states, photo presence, and a structured read of the most
# salient fields. Merged into extracted_fields under a `vision` key + a few
# promoted booleans the anomaly validators can use.

_VISION_EXTRACT_PROMPT = get_prompt("vision_extract")


def _vision_json_call(
    image_bytes: bytes, mime: str, prompt: str,
    *, db: Session | None = None, tenant_id: str | None = None,
) -> dict | None:
    """Single vision call that asks for STRICT JSON and parses it (tolerant via
    json-repair). Returns a dict or None on failure. Reliability-first cascade:
    PAID Qwen-VL (Dashscope) → PAID Claude-Haiku (OpenRouter). Structured
    extraction favours the reliable paid tiers over the free one."""
    import json as _json
    from json_repair import repair_json

    def _parse(text: str) -> dict | None:
        if not text:
            return None
        try:
            return _json.loads(text)
        except Exception:  # noqa: BLE001 — tolerate fenced / trailing-comma JSON
            try:
                return _json.loads(repair_json(text))
            except Exception:  # noqa: BLE001
                return None

    settings = get_settings()
    # 1. PAID: Qwen-VL via Dashscope (reliable structured vision).
    if settings.dashscope_api_key:
        parsed = _parse(_qwen_vision_raw(
            image_bytes, mime, prompt, max_tokens=1200, task="vision_extract",
            db=db, tenant_id=tenant_id, settings=settings))
        if parsed is not None:
            return parsed
        log.info("vision-extract: Qwen empty/unparsable → Claude-Haiku fallback")
    # 2. PAID fallback: Claude-Haiku via OpenRouter.
    if not settings.openrouter_api_key:
        return None
    body = {
        "model": _CLAUDE_MODEL,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": _image_data_url(image_bytes, mime)}},
            ],
        }],
        "max_tokens": 1200,
    }
    headers = {
        "Authorization": f"Bearer {settings.openrouter_api_key}",
        "HTTP-Referer": settings.openrouter_referer,
        "X-Title": settings.openrouter_app_title,
        "Content-Type": "application/json",
    }
    t0 = _time.perf_counter()
    try:
        resp = httpx.post(_URL, json=body, headers=headers, timeout=_VISION_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        text = (data["choices"][0]["message"]["content"] or "").strip()
        if db is not None:
            usage = data.get("usage") or {}
            ledger.record_call(
                db, task="vision_extract", tier="t2", provider="openrouter", model=_CLAUDE_MODEL,
                input_tokens=int(usage.get("prompt_tokens", 0)),
                output_tokens=int(usage.get("completion_tokens", 0)),
                cost_per_input_mtok=1.0, cost_per_output_mtok=5.0,
                latency_ms=int((_time.perf_counter() - t0) * 1000),
                status="ok", tenant_id=tenant_id,
            )
        return _parse(text)
    except Exception as e:  # noqa: BLE001
        log.warning("vision-extract: call failed: %s", e)
        if db is not None:
            ledger.record_call(
                db, task="vision_extract", tier="t2", provider="openrouter", model=_CLAUDE_MODEL,
                status="failed", error=str(e),
                latency_ms=int((_time.perf_counter() - t0) * 1000), tenant_id=tenant_id,
            )
        # 3. FREE fallback: Gemini (terminal — best-effort when paid tiers are down).
        if settings.google_genai_api_key:
            log.info("vision-extract: Claude-Haiku failed → Gemini terminal fallback")
            from app.feature_flags import get_str as _gs
            text = _vision_transcribe_gemini(
                image_bytes, mime, db=db, tenant_id=tenant_id, settings=settings,
                prompt=prompt,
            )
            parsed = _parse(text)
            if parsed is not None:
                return parsed
            log.warning("vision-extract: ALL fallbacks exhausted — returning None")
        return None


# G10 · figure / chart extraction prompt. Asks the VLM to read a chart/figure on
# the page and return its underlying data + a one-line takeaway — so charts (which
# carry zero extractable text) become queryable structured data.
_FIGURE_PROMPT = get_prompt("vision_figure")

# Kinds that belong to G8 (tables) or are non-data noise (logos/photos). Dropped
# from G10 so figures stay charts/diagrams only — no duplication with tables.
_FIGURE_DROP_KINDS = {"table", "logo", "letterhead", "stamp", "signature", "photo", "text"}


def _page_has_figure(page) -> bool:
    """True when a page plausibly contains a chart/diagram — a large raster image
    OR enough vector chart geometry (filled paths / curves). Cheap, no VLM call;
    gates which pages are worth a (paid) vision read in extract_figures."""
    page_area = abs(page.rect.width * page.rect.height) or 1.0
    try:
        for inf in page.get_image_info():
            b = inf.get("bbox")
            if b and abs((b[2] - b[0]) * (b[3] - b[1])) >= 0.12 * page_area:
                return True
    except Exception:  # noqa: BLE001
        pass
    try:
        chartish = 0
        for d in page.get_drawings():
            if d.get("fill") is not None or any(it and it[0] == "c" for it in d.get("items", [])):
                chartish += 1
                if chartish >= 6:   # several filled/curved paths ⇒ a chart
                    return True
    except Exception:  # noqa: BLE001
        pass
    return False
    # Note: recall-first — we tolerate occasional false-positive pages (decorated
    # invoices) since they're bounded by max_pages and the VLM just returns []. The
    # alternative (size-gating vector paths) dropped real vector pie charts.


def extract_figures(
    pdf_bytes: bytes, *, max_pages: int = 8, max_edge: int | None = None,
    db: Session | None = None, tenant_id: str | None = None,
) -> list[dict]:
    """G10 · for pages that contain figures/charts (detected via embedded images),
    rasterise the page and ask the VLM to extract the figure's data. Returns a
    flat list of figure dicts each tagged with its 1-based `page`. Best-effort +
    bounded (max_pages) to cap vision cost. Empty list on any failure / no figures.

    `max_edge` caps the image resolution sent to the VLM (input-token cost lever);
    defaults to settings.documents_figure_max_edge.
    """
    fig_max_edge = max_edge or get_int("documents_figure_max_edge", 1280)
    out: list[dict] = []
    try:
        import fitz
        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            # A page plausibly carries a chart when it has EITHER a large raster
            # image (≥12% of the page — skips logos/letterheads) OR enough VECTOR
            # chart geometry: filled paths / bezier curves (pie slices, bars, line
            # curves). Plain tables use unfilled straight strokes, so they don't
            # trip it. This avoids paying for vision on pure-text / logo-only pages
            # while still catching vector-drawn charts (common in finance reports).
            fig_pages = [i for i in range(doc.page_count)
                         if _page_has_figure(doc.load_page(i))]
            import hashlib
            seen_img_hashes: set[str] = set()  # dedup repeated figures/letterheads across pages
            for i in fig_pages[:max_pages]:
                page = doc.load_page(i)
                # Cost control: if every sizeable embedded image on this page was
                # already sent to the VLM on an earlier page (a figure/logo/watermark
                # repeated across pages), skip — caption it once, don't pay again.
                # Vector-only chart pages (no embedded raster) never trip this.
                try:
                    big_hashes = []
                    for im in page.get_images(full=True):
                        ex = doc.extract_image(im[0])
                        if ex and ex.get("image") and len(ex["image"]) > 4096:
                            big_hashes.append(hashlib.md5(ex["image"]).hexdigest())
                    if big_hashes and all(h in seen_img_hashes for h in big_hashes):
                        continue
                    seen_img_hashes.update(big_hashes)
                except Exception:  # noqa: BLE001 — dedup is best-effort
                    pass
                pix = page.get_pixmap(matrix=fitz.Matrix(2.0, 2.0), alpha=False)
                # Cost lever: figures are legible well below OCR resolution, and
                # Qwen-VL input tokens scale with image area. Cap the figure image
                # smaller than the OCR path (configurable) → ~⅓–½ fewer input tokens
                # per chart page. Charts/diagrams stay readable at ~1280px.
                img = prepare_image_for_vision(
                    pix.tobytes("png"), "image/png", max_edge=fig_max_edge)
                if img is None:
                    continue
                parsed = _vision_json_call(img[0], img[1], _FIGURE_PROMPT, db=db, tenant_id=tenant_id)
                figs = (parsed or {}).get("figures") or []
                for f in figs:
                    if not isinstance(f, dict):
                        continue
                    kind = str(f.get("kind") or "").strip().lower()
                    # Tables → G8's domain; logos/photos → noise. Keep only charts
                    # /diagrams that carry actual data (summary or data_points).
                    if kind in _FIGURE_DROP_KINDS:
                        continue
                    if f.get("summary") or f.get("data_points"):
                        f["page"] = i + 1
                        out.append(f)
    except Exception as e:  # noqa: BLE001
        log.warning("figure-extract failed (non-fatal): %s", e)
    return out


def _first_page_image(raw_bytes: bytes, mime: str) -> tuple[bytes, str] | None:
    """Return (image_bytes, mime) for the doc's first visual page — rasterise
    a PDF page at 2x, or transcode an image. None if it can't be imaged."""
    m = _normalise_mime(mime or "")
    if m.startswith("image/"):
        # Downscale/re-encode so large images don't 400 the vision provider.
        return prepare_image_for_vision(raw_bytes, m)
    # treat as PDF
    try:
        import fitz
        with fitz.open(stream=raw_bytes, filetype="pdf") as doc:
            if len(doc) == 0:
                return None
            pix = doc.load_page(0).get_pixmap(matrix=fitz.Matrix(2.0, 2.0), alpha=False)
            # A 2x pixmap of a large page can also exceed limits — cap it.
            return prepare_image_for_vision(pix.tobytes("png"), "image/png")
    except Exception as e:  # noqa: BLE001
        log.warning("vision-extract: rasterise failed: %s", e)
        return None


def vision_extract_fields(
    raw_bytes: bytes, mime: str,
    *, db: Session | None = None, tenant_id: str | None = None,
) -> dict | None:
    """P9.4 · run a vision-mode read of the document's first page. Returns the
    parsed JSON dict (signature_present, stamps, checkboxes, key_fields, …) or
    None. Best-effort — callers merge into extracted_fields; failure is silent."""
    img = _first_page_image(raw_bytes, mime)
    if img is None:
        return None
    image_bytes, img_mime = img
    return _vision_json_call(
        image_bytes, img_mime, _VISION_EXTRACT_PROMPT, db=db, tenant_id=tenant_id,
    )


# G11 · multi-pass OCR voting -------------------------------------------------

def _best_transcript(candidates: list[str]) -> str:
    """Pick the highest-quality transcript among independent OCR passes, scored by
    the G3 page-quality heuristic. Pure + deterministic → unit-testable without a
    VLM. Empty/whitespace candidates are ignored; returns '' if none usable."""
    from app.ocr_quality import page_quality
    usable = [c for c in candidates if c and c.strip()]
    if not usable:
        return ""
    return max(usable, key=lambda t: page_quality(t).score)


def transcribe_page(
    png: bytes, mime: str = "image/png",
    *, db: Session | None = None, tenant_id: str | None = None,
) -> str:
    """OCR one rendered page. Single pass by default; when multi-pass OCR is
    enabled (DOCAIQ_DOCUMENTS_MULTIPASS_OCR) AND the primary pass scores
    low-confidence (G3), run a SECOND independent pass with a different engine and
    keep the higher-quality transcript. Cost is bounded: the 2nd pass only fires
    on pages that actually look bad, and only one extra call."""
    settings = get_settings()
    primary = _vision_transcribe_one(png, mime, db=db, tenant_id=tenant_id)
    if not is_enabled("documents_multipass_ocr", False):
        return primary
    from app.ocr_quality import REVIEW_THRESHOLD, page_quality
    if primary and page_quality(primary).score >= REVIEW_THRESHOLD:
        return primary  # already good — don't pay for a 2nd pass
    # Low-confidence → ONE 2nd opinion from a DISTINCT engine, then vote. The
    # paid primary is almost always Qwen-VL (Gemini leads the cascade only when a
    # free key is set), so we prefer Claude-Haiku as the alternate — a genuinely
    # different model. Falls back to Gemini, then Qwen, by key availability.
    # Cost is bounded to exactly one extra call, and only on low-quality pages.
    candidates = [primary]
    try:
        alt = ""
        if settings.openrouter_api_key:
            alt = _vision_transcribe_claude(png, mime, db=db, tenant_id=tenant_id, settings=settings)
        elif settings.google_genai_api_key and not _gemini_circuit_open():
            alt = _vision_transcribe_gemini(png, mime, db=db, tenant_id=tenant_id, settings=settings)
        elif settings.dashscope_api_key:
            alt = _vision_transcribe_qwen(png, mime, db=db, tenant_id=tenant_id, settings=settings)
        if alt:
            candidates.append(alt)
    except Exception as e:  # noqa: BLE001 — best-effort 2nd pass
        log.warning("multipass-ocr: 2nd pass failed (non-fatal): %s", e)
    return _best_transcript(candidates) or primary


# ── Phase 2 · structured-vision transcription (Document Model) ──────────────
# Ask the VLM for faithful GitHub-Flavored Markdown so form fields, tables and
# headings survive as *structure* (parsed into typed IR blocks by markdown_ir).
# This is what keeps 'Race: INDIAN' bound on a scanned NRIC — the flat OCR prompt
# dropped the label, mislabelling nationality (#302).
_MD_INGEST_PROMPT = get_prompt("md_ingest")


def transcribe_page_markdown(
    png: bytes, mime: str = "image/png",
    *, db: Session | None = None, tenant_id: str | None = None,
) -> str:
    """Phase 2 · structured-vision: transcribe one page to GFM Markdown (single pass)
    so it parses into typed IR blocks. Strips stray whole-page code fences. '' on
    failure (caller keeps the page empty)."""
    md = _vision_transcribe_one(png, mime, db=db, tenant_id=tenant_id, prompt=_MD_INGEST_PROMPT)
    return re.sub(r"^```(?:markdown|md)?\s*|\s*```$", "", (md or "").strip()).strip()


def parse_pdf_via_vision(
    pdf_bytes: bytes, *, max_pages: int | None = None,
    db: Session | None = None, tenant_id: str | None = None,
    structured: bool = False,
) -> list[tuple[int, str]]:
    """Rasterise each page of an image-only PDF (no text layer) and OCR them
    via vision. Returns the same shape as parse_pdf() so the rest of the
    ingestion pipeline doesn't need to know which path produced the text.

    Caps at config.documents_max_ocr_pages (default 100) to bound cost on
    pathologically long scanned docs. Pages above the cap come back with an
    [over OCR cap] marker — the user can re-upload split to get the rest.
    """
    import fitz  # PyMuPDF

    from app.config import get_settings
    config_cap = get_int("documents_max_ocr_pages", 100) or _MAX_PAGES_PER_DOC
    cap = min(max_pages or config_cap, config_cap)
    pages: list[tuple[int, str]] = []
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        total = len(doc)
        for i in range(min(total, cap)):
            page = doc.load_page(i)
            # 2x scaling helps OCR accuracy without blowing up token cost
            pix = page.get_pixmap(matrix=fitz.Matrix(2.0, 2.0), alpha=False)
            png = pix.tobytes("png")
            text = (transcribe_page_markdown if structured else transcribe_page)(
                png, "image/png", db=db, tenant_id=tenant_id)
            if text:
                pages.append((i + 1, text))
            else:
                pages.append((i + 1, "[vision OCR returned no text]"))
        if total > cap:
            # T3.2 · Make the cap loud — both in the chunk text (where
            # retrieval will see it) and in the worker log (so operators
            # notice the silent recall loss). Ingestion callers can also
            # propagate this to documents.ingestion_error if they detect
            # the marker text.
            msg = (
                f"[VISION OCR CAP HIT · doc has {total} pages, processed first {cap}. "
                "Re-upload remaining pages as a separate document to get full coverage.]"
            )
            log.warning(
                "vision OCR cap hit · total=%d cap=%d (set DOCAIQ_DOCUMENTS_MAX_OCR_PAGES or split the doc)",
                total, cap,
            )
            pages.append((cap + 1, msg))
    return pages


def parse_pdf_pages_via_vision(
    pdf_bytes: bytes, *, page_indexes_1based: list[int],
    db: Session | None = None, tenant_id: str | None = None,
    structured: bool = False,
) -> list[tuple[int, str]]:
    """Selectively OCR only the listed page indexes (1-based). Used when a
    PDF has a text layer on SOME pages but is image-only on others
    (mixed-mode statements). Returns [(page_no, text), ...] for the
    requested pages only — caller merges with the text from the
    PyMuPDF path."""
    import fitz  # PyMuPDF

    pages: list[tuple[int, str]] = []
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        for p in page_indexes_1based:
            i = p - 1
            if i < 0 or i >= len(doc):
                continue
            page = doc.load_page(i)
            pix = page.get_pixmap(matrix=fitz.Matrix(2.0, 2.0), alpha=False)
            png = pix.tobytes("png")
            text = (transcribe_page_markdown if structured else transcribe_page)(
                png, "image/png", db=db, tenant_id=tenant_id)
            pages.append((p, text or "[vision OCR returned no text]"))
    return pages


def pdf_has_extractable_text(pdf_bytes: bytes) -> bool:
    """Quick check: does this PDF have ANY extractable text via PyMuPDF?
    Used to decide whether to fall back to vision OCR. We sample up to the
    first 3 pages to keep it cheap on 200-page docs."""
    import fitz

    try:
        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            for i in range(min(len(doc), 3)):
                page = doc.load_page(i)
                text = (page.get_text("text") or "").strip()
                if len(text) >= 40:
                    return True
        return False
    except Exception:  # noqa: BLE001
        return False
