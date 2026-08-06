"""Translation service — LLM-based markdown translation preserving block markers.

Pure functions: take db + plain args, return data. No HTTP concerns.
Uses the existing LLM gateway (DashScope) for page-by-page translation with a
prompt engineered to preserve ``<!-- block:b_XXXX -->`` markers and markdown
structure. Each page is translated independently so large documents never hit
API input limits or time out.

Results are cached in the document's ``translations`` JSONB column.
"""
from __future__ import annotations

import datetime as _dt
import logging
import re

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.config import get_settings
from app.llm.gateway import Message, call as llm_call

log = logging.getLogger("docaiq.translation")

# ── Supported target languages ──────────────────────────────────────────────
SUPPORTED_LANGUAGES: dict[str, str] = {
    "fr": "French",
    "de": "German",
    "es": "Spanish",
    "it": "Italian",
    "pt": "Portuguese",
    "nl": "Dutch",
    "zh": "Chinese (Simplified)",
    "ja": "Japanese",
    "ko": "Korean",
    "ar": "Arabic",
    "ru": "Russian",
    "pl": "Polish",
    "tr": "Turkish",
    "vi": "Vietnamese",
    "th": "Thai",
    "hi": "Hindi",
}

from app.llm.prompts import get_prompt


def _strip_code_fences(md: str) -> str:
    """Remove accidental surrounding ``` fences the model may add."""
    md = md.strip()
    md = re.sub(r'^```(?:markdown|md)?\s*\n?', '', md)
    md = re.sub(r'\n?```\s*$', '', md)
    return md.strip()


def _split_pages(md: str) -> list[str]:
    """Split annotated markdown into pages at ``## Page N`` boundaries.
    The title block (everything before the first ``## Page N``) becomes page 0."""
    # Split on "## Page N" — keep the delimiter with the page that follows
    parts = re.split(r'(## Page \d+\s*\n)', md)
    if len(parts) <= 1:
        return [md]  # single page, no page headings

    pages: list[str] = []
    # parts[0] = title + any text before first ## Page N
    title_block = parts[0].strip()
    i = 1
    while i < len(parts):
        if re.match(r'## Page \d+\s*\n', parts[i]):
            header = parts[i]
            body = parts[i + 1] if i + 1 < len(parts) else ""
            page_md = (header + body).strip()
            if page_md:
                pages.append(page_md)
            i += 2
        else:
            # stray text — append to last page or start a new one
            if parts[i].strip():
                if pages:
                    pages[-1] = pages[-1] + "\n" + parts[i]
                else:
                    pages.append(parts[i])
            i += 1

    # If there was meaningful content before the first page heading, prepend it
    # (it usually contains the document title which we want on page 0)
    if title_block:
        # Strip the initial "# Title" line from title_block — we'll keep the
        # first page heading instead.  But if there's substantial content there
        # (e.g. a single-page doc with no page breaks), include it.
        if not pages:
            return [title_block]
        # Check if title_block is just a bare "# Title" with nothing else
        title_only = title_block.strip()
        if title_only.startswith("#") and "\n" not in title_only:
            # Bare title — prepend to first page as context
            pages[0] = title_only + "\n\n" + pages[0]
        else:
            # Has content — add as first page
            pages.insert(0, title_block)

    return pages if pages else [md]


def _translate_one_page(
    page_md: str,
    target_language: str,
    lang_label: str,
    previous_page_tail: str | None,
    model: str,
    tenant_id: str | None,
    user_email: str | None,
    doc_id_external: str | None,
    page_num: int,
    total_pages: int,
) -> str:
    """Translate a single page of markdown.  Optionally passes the tail of the
    previous page as context so the translation is coherent across boundaries."""
    system = get_prompt("translate",
        target_language=lang_label, lang_code=target_language,
    )

    if previous_page_tail:
        user = get_prompt("translate_context",
            target_language=lang_label,
            previous_tail=previous_page_tail[-400:],
        ) + page_md
    else:
        user = get_prompt("translate_page", target_language=lang_label) + page_md

    log.debug("page %d/%d: %d chars", page_num + 1, total_pages, len(page_md))

    result = llm_call(
        model=model,
        messages=[
            Message(role="system", content=system),
            Message(role="user", content=user),
        ],
        temperature=0.2,
        max_tokens=4096,
        tenant_id=tenant_id,
        user_email=user_email,
        doc_id_external=doc_id_external,
        task_kind="translate",
    )

    translated = _strip_code_fences(result.text)
    if not translated.strip():
        raise RuntimeError(f"LLM returned empty translation for page {page_num + 1}")

    return translated


def get_translation(doc, target_language: str) -> dict | None:
    """Retrieve a cached translation for the given language, or None."""
    translations = getattr(doc, "translations", None) or {}
    entry = translations.get(target_language)
    if isinstance(entry, dict) and entry.get("body"):
        return entry
    return None


def list_translations(doc) -> dict[str, dict]:
    """Return all available translation language codes with metadata."""
    translations = getattr(doc, "translations", None) or {}
    return {
        lang: {
            "translated_at": info.get("translated_at"),
            "model": info.get("model"),
            "status": info.get("status", "complete"),
        }
        for lang, info in translations.items()
        if isinstance(info, dict) and info.get("body")
    }


def translate_markdown(
    db: Session,
    doc,
    target_language: str,
    *,
    tenant_id: str | None = None,
    user_email: str | None = None,
) -> dict:
    """Translate a document's markdown to the target language, preserving
    block markers and markdown structure.

    Splits the document into pages at ``## Page N`` boundaries and translates
    each page independently so large documents never hit API input limits or
    time out.  Each page gets the tail of the previous page as context so
    translation is coherent across page boundaries.

    Checks the ``doc.translations`` JSONB cache first; on cache miss, calls
    the LLM gateway (DashScope Qwen), stores the result, and returns it.

    Returns:
        {"body": str, "annotated_body": str, "translated_at": str,
         "model": str, "language": str, "cached": bool, "pages": int}
    """
    from app.services import markdown_export

    # ── Cache hit? ──────────────────────────────────────────────────────
    cached = get_translation(doc, target_language)
    if cached:
        log.info("Translation cache hit: doc=%s lang=%s", doc.id_external, target_language)
        return {**cached, "cached": True}

    # ── Get the source markdown ─────────────────────────────────────────
    # Prefer vision-rendered markdown (rich GFM: tables, headings, lists) over
    # the raw chunk-based render (flat paragraphs).  Falls back gracefully.
    source_kind = "chunks"  # track which source we used
    source_body: str | None = None

    # 1. Vision-rendered (cached) — best formatting, best translation quality
    vision_md = getattr(doc, "rendered_markdown", None) or None
    if vision_md:
        source_body = vision_md
        source_kind = "vision"
        log.info("Using vision-rendered markdown for translation: doc=%s", doc.id_external)

    # 2. Annotated markdown (block-map based) — with block markers for PDF sync
    block_map = getattr(doc, "block_map", None) or None
    if not source_body and block_map:
        annotated_body = markdown_export.build_annotated_markdown(db, doc)
        if annotated_body:
            source_body = annotated_body
            source_kind = "annotated"

    # 3. Full markdown (chunk-based) — always available, basic formatting
    if not source_body:
        source_body = markdown_export.build_full_markdown(db, doc)
        source_kind = "chunks"

    if not source_body:
        raise ValueError("No markdown available for this document")

    # ── Split into pages ────────────────────────────────────────────────
    pages = _split_pages(source_body)
    total_pages = len(pages)
    lang_label = SUPPORTED_LANGUAGES.get(target_language, target_language)
    model = get_settings().intelligence_model
    doc_id_ext = doc.id_external if hasattr(doc, "id_external") else None

    log.info(
        "Translating doc=%s to %s via %s — %d page(s), %d chars total (source=%s)",
        doc_id_ext, target_language, model, total_pages, len(source_body), source_kind,
    )

    # ── Translate each page ─────────────────────────────────────────────
    total_input_tokens = 0
    total_output_tokens = 0
    translated_pages: list[str] = []
    previous_tail: str | None = None

    for i, page_md in enumerate(pages):
        try:
            result = llm_call(
                model=model,
                messages=[
                    Message(
                        role="system",
                        content=get_prompt("translate",
                            target_language=lang_label,
                            lang_code=target_language,
                        ),
                    ),
                    Message(
                        role="user",
                        content=(
                            get_prompt("translate_context",
                                target_language=lang_label,
                                previous_tail=previous_tail[-400:],
                            ) + page_md
                            if previous_tail
                            else get_prompt("translate_page",
                                target_language=lang_label,
                            ) + page_md
                        ),
                    ),
                ],
                temperature=0.2,
                max_tokens=4096,
                tenant_id=tenant_id,
                user_email=user_email,
                doc_id_external=doc_id_ext,
                task_kind="translate",
            )

            translated = _strip_code_fences(result.text)
            if not translated.strip():
                raise RuntimeError(f"Empty translation for page {i + 1}")

            translated_pages.append(translated)
            total_input_tokens += result.input_tokens
            total_output_tokens += result.output_tokens

            # Use the tail of THIS source page as context for the next page
            previous_tail = page_md

            log.debug(
                "page %d/%d done — %d→%d chars, %d/%d tokens",
                i + 1, total_pages, len(page_md), len(translated),
                result.input_tokens, result.output_tokens,
            )
        except Exception:
            log.exception("Translation failed on page %d/%d of doc=%s", i + 1, total_pages, doc_id_ext)
            raise RuntimeError(
                f"Translation failed on page {i + 1} of {total_pages}. "
                "The document may be too complex for this page — try a smaller document."
            )

    translated_md = "\n\n".join(translated_pages)

    # ── Warn if block markers were dropped ──────────────────────────────
    orig_markers = set(re.findall(r'<!-- block:b_\w+(?:_r\d+_c\d+)? -->', source_body))
    trans_markers = set(re.findall(r'<!-- block:b_\w+(?:_r\d+_c\d+)? -->', translated_md))
    missing = orig_markers - trans_markers
    extra = trans_markers - orig_markers
    if missing:
        log.warning(
            "Translation dropped %d block markers: doc=%s lang=%s",
            len(missing), doc_id_ext, target_language,
        )
    if extra:
        log.warning(
            "Translation added %d unexpected block markers: doc=%s lang=%s",
            len(extra), doc_id_ext, target_language,
        )

    # ── Cache the translation ───────────────────────────────────────────
    entry = {
        "body": translated_md,
        "annotated_body": translated_md,
        "translated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "model": model,
        "status": "complete",
        "language": target_language,
        "input_tokens": total_input_tokens,
        "output_tokens": total_output_tokens,
        "pages": total_pages,
        "source": source_kind,
        "cached": False,
    }

    translations = dict(getattr(doc, "translations", None) or {})
    translations[target_language] = entry
    doc.translations = translations
    flag_modified(doc, "translations")
    db.commit()

    log.info(
        "Translation cached: doc=%s lang=%s pages=%d tokens=%d/%d",
        doc_id_ext, target_language, total_pages,
        total_input_tokens, total_output_tokens,
    )

    return entry
