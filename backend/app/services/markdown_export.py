"""Deterministic whole-document → Markdown, reconstructed from the parsed text chunks.

No LLM, no page cap, owner-viewable — unlike the legacy `POST /documents/{id}/markdown`
(LLM-generated, capped ~30 pages, admin/reviewer-only). This is the "extract any whole
document as a clean .md" path.

v1 uses the `text` chunks only: they carry the full document content in reading order.
The layout-derived `table` chunks DUPLICATE that same content (and are mostly page layout
captured as 2-column tables, not clean data tables), so including them would just add noise
and repetition. Clean GFM data-table rendering is a v2 refinement that rides on better table
detection (the PaddleOCR-VL vision tier).
"""
from __future__ import annotations

import logging
import re as _re
import time as _time

import httpx
from sqlalchemy import select

from app.llm.prompts import get_prompt
from app.model_registry import REGISTRY as _AI_REGISTRY
from app.orm import DocumentChunk  # noqa: F401 (re-exported / used below)

log = logging.getLogger("docaiq.markdown_export")

_DEEPSEEK_MODEL = _AI_REGISTRY["markdown_enhance"].default_model
_DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"
_DEEPSEEK_TIMEOUT = 60.0

_DEEPSEEK_ENHANCE_PROMPT = get_prompt("deepseek_enhance")
_MD_VISION_PROMPT = get_prompt("md_vision")


def _enhance_with_deepseek(md_text: str, *, title: str = "", db=None, tenant_id=None) -> str:
    """Post-process vision markdown with DeepSeek V4 Flash for OCR cleanup and formatting.

    Tries DeepSeek direct API first (when DOCAIQ_DEEPSEEK_API_KEY is set), then
    falls back to OpenRouter. Returns enhanced markdown, or the original text on
    any failure (non-blocking — the pipeline never breaks on post-processing)."""
    from app.config import get_settings
    from app.llm import ledger

    settings = get_settings()

    messages = [
        {"role": "system", "content": get_prompt("md_enhance")},
        {"role": "user", "content": f"{_DEEPSEEK_ENHANCE_PROMPT}\n\n{md_text}"},
    ]
    common_body = {
        "messages": messages,
        "max_tokens": 8000,
        "temperature": 0.1,
        "thinking": {"type": "disabled"},  # V4 Flash defaults to thinking mode → empty content
    }

    def _call(provider: str, model: str, url: str, api_key: str,
              extra_headers: dict | None = None,
              cost_in: float = 0.14, cost_out: float = 0.28) -> str | None:
        """One attempt. Returns text on success, None on failure."""
        body = {"model": model, **common_body}
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        if extra_headers:
            headers.update(extra_headers)

        t0 = _time.perf_counter()
        try:
            resp = httpx.post(url, json=body, headers=headers, timeout=_DEEPSEEK_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            text = (data["choices"][0]["message"]["content"] or "").strip()
            text = _re.sub(r"^```(?:markdown|md)?\s*|\s*```$", "", text).strip()

            if db is not None:
                usage = data.get("usage") or {}
                ledger.record_call(
                    db, task="vision_post", tier="t3", provider=provider,
                    model=model,
                    input_tokens=int(usage.get("prompt_tokens", 0)),
                    output_tokens=int(usage.get("completion_tokens", 0)),
                    cost_per_input_mtok=cost_in, cost_per_output_mtok=cost_out,
                    latency_ms=int((_time.perf_counter() - t0) * 1000),
                    status="ok", tenant_id=tenant_id,
                )

            # Guard 1: length check — model truncated or returned empty
            if len(text) < len(md_text) * 0.4:
                log.warning("vision_post: %s output too short (%d vs %d chars) — discarding",
                            provider, len(text), len(md_text))
                return None

            # Guard 2: data integrity — extract IDs/codes/amounts from original
            # and verify ≥ 90% survive in the enhanced version (catches hallucination)
            _data_tokens = set(_re.findall(
                r"\b[A-Z0-9]{6,}\b|\$\d+(?:\.\d{2})?|\b\d{5,}\b", md_text
            ))
            if _data_tokens:
                surviving = sum(1 for t in _data_tokens if t in text)
                rate = surviving / len(_data_tokens)
                if rate < 0.9:
                    missing = _data_tokens - {t for t in _data_tokens if t in text}
                    log.warning(
                        "vision_post: %s data integrity check failed — %.0f%% (%d/%d) IDs/numbers preserved, "
                        "missing: %s — discarding",
                        provider, rate * 100, surviving, len(_data_tokens),
                        ", ".join(sorted(missing)[:5]),
                    )
                    return None

            log.info("vision_post: %s enhanced markdown — %d → %d chars, %.1fs",
                     provider, len(md_text), len(text), _time.perf_counter() - t0)
            return text

        except Exception as e:
            log.warning("vision_post: %s call failed (non-blocking): %s", provider, e)
            if db is not None:
                try:
                    ledger.record_call(
                        db, task="vision_post", tier="t3", provider=provider,
                        model=model, status="failed", error=str(e),
                        latency_ms=int((_time.perf_counter() - t0) * 1000),
                        tenant_id=tenant_id,
                    )
                except Exception:
                    pass
            return None

    # ── DeepSeek direct API ──
    if settings.deepseek_api_key:
        result = _call(
            "deepseek", _DEEPSEEK_MODEL, _DEEPSEEK_URL, settings.deepseek_api_key,
        )
        if result:
            return result
        log.info("vision_post: DeepSeek direct failed — returning original")

    return md_text


def _strip_empty_table_rows(md: str) -> str:
    """Drop table rows that are all-empty cells (vision models emit these for blank ruled areas).
    Keeps the header separator (|---|) and any row with real content."""
    def _empty(line: str) -> bool:
        s = line.strip()
        return s.startswith("|") and s.replace("|", "").replace(" ", "").replace("\t", "") == ""
    return "\n".join(ln for ln in md.split("\n") if not _empty(ln))


def build_vision_markdown(db, doc, *, max_pages: int = 15) -> str | None:
    """Faithful whole-document Markdown via a vision model (qwen-vl) per page — headings, GFM
    tables, and lists that mirror the original. Costs N vision calls; the caller caches the
    result. Returns ``None`` when vision is unavailable (no source file, all pages blank, …);
    the caller should fall back to the deterministic text render."""
    from app import storage
    from app.ingestion_vision import _vision_transcribe_one

    key = getattr(doc, "s3_key", None)
    raw = storage.get_object_bytes(key) if key else None
    if not raw:
        return None
    mime = (getattr(doc, "mime_type", "") or "").lower()
    tid = getattr(doc, "tenant_id", None)
    title = getattr(doc, "name", None) or "Document"

    def _page_md(png: bytes, m: str) -> str:
        try:
            # Cost-aware cascade: FREE Gemini → PAID Qwen-VL → PAID Claude
            return (_vision_transcribe_one(png, m, db=db, tenant_id=tid,
                                           prompt=_MD_VISION_PROMPT) or "").strip()
        except Exception as e:  # noqa: BLE001
            log.warning("vision markdown: page failed (non-fatal): %s", e)
            return ""

    parts: list[str] = []
    if mime.startswith("image/"):
        parts.append(_page_md(raw, mime or "image/png"))
    else:  # PDF (or anything fitz can open) → rasterise each page at 2x
        try:
            import fitz  # PyMuPDF
            with fitz.open(stream=raw, filetype="pdf") as pdf:
                n = min(len(pdf), max_pages)
                for i in range(n):
                    pix = pdf.load_page(i).get_pixmap(matrix=fitz.Matrix(2.0, 2.0), alpha=False)
                    md = _page_md(pix.tobytes("png"), "image/png")
                    if md:
                        parts.append(md)
        except Exception as e:  # noqa: BLE001
            log.warning("vision markdown: pdf render failed: %s", e)
            return None

    body = "\n\n---\n\n".join(p for p in parts if p).strip()
    body = _re.sub(r"^```(?:markdown|md)?\s*|\s*```$", "", body).strip()
    body = _strip_empty_table_rows(body)
    if not body:
        return None

    # Post-process with DeepSeek V4 Flash: fix OCR errors, normalize formatting.
    # Non-blocking — returns original on any failure.
    body = _enhance_with_deepseek(body, title=title, db=db, tenant_id=tid)

    return f"# {title}\n\n{body}\n"



def render_markdown(title: str, chunks) -> str:
    """Pure renderer (unit-testable). `chunks` is an ordered iterable of objects with
    `.text`, `.page`, `.kind`. Emits a title, per-page `## Page N` sections, and the text
    as paragraphs. Blank chunks are skipped; missing page numbers omit the page heading."""
    parts: list[str] = [f"# {title or 'Document'}"]
    cur_page = object()  # sentinel != any real page (incl. None)
    for c in chunks:
        text = (getattr(c, "text", "") or "").strip()
        if not text:
            continue
        page = getattr(c, "page", None)
        if page is not None and page != cur_page:
            cur_page = page
            parts.append(f"\n---\n\n## Page {page}")
        parts.append(f"\n{text}")
    body = "\n".join(parts).strip()
    return (body + "\n") if body.strip() != f"# {title or 'Document'}" else ""


def build_full_markdown(db, doc) -> str:
    """Query the document's text chunks (reading order) and render deterministic Markdown."""
    chunks = db.scalars(
        select(DocumentChunk)
        .where(DocumentChunk.document_pk == doc.pk, DocumentChunk.kind == "text")
        .order_by(DocumentChunk.page, DocumentChunk.chunk_index)
    ).all()
    return render_markdown(getattr(doc, "name", None) or "Document", chunks)


def _split_table_cells(text: str) -> list[list[str]] | None:
    """Parse a GFM pipe table into rows × cells.  Returns a list of rows (each a
    list of cell strings), or None when the text doesn't look like a pipe table."""
    lines = text.strip().split("\n")
    rows: list[list[str]] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        # Skip separator lines (|---|---|…)
        stripped = line.replace("|", "").replace("-", "").replace(":", "").replace(" ", "").replace("\t", "")
        if stripped == "":
            continue
        if "|" not in line:
            return None
        cells = [c.strip() for c in line.split("|")]
        if cells and cells[0] == "":
            cells = cells[1:]
        if cells and cells[-1] == "":
            cells = cells[:-1]
        if cells:
            rows.append(cells)
    return rows if rows else None


def build_annotated_markdown(db, doc) -> str | None:
    """Build markdown from the block_map (Docling IR blocks), injecting
    ``<!-- block:id -->`` markers so the frontend can make sections clickable
    and tie them to per-block PDF bboxes.

    For table-kind blocks, per-cell markers (``_rN_cN``) are emitted so each
    cell links to its approximate sub-region of the parent table bbox.

    Returns None when the document has no block_map — callers fall back to
    ``build_full_markdown`` (flat chunk-based rendering)."""
    bm = getattr(doc, "block_map", None)
    if not bm:
        return None
    # Sort block IDs by their index to preserve Docling reading order
    def _idx(bid: str) -> int:
        try:
            return int(bid.split("_")[1], 10)
        except (IndexError, ValueError):
            return 0
    ordered = sorted(bm.items(), key=lambda kv: _idx(kv[0]))

    title = getattr(doc, "name", None) or "Document"
    parts: list[str] = [f"# {title}"]
    cur_page = object()
    for block_id, info in ordered:
        txt = (info.get("text", "") or "").strip()
        if not txt:
            continue
        page = info.get("page")
        if page is not None and page != cur_page:
            cur_page = page
            parts.append(f"\n---\n\n## Page {page}")

        # ── Table-kind blocks: emit per-cell markers ───────────────────────
        if (info.get("kind") or "").lower() == "table":
            rows = _split_table_cells(txt)
            if rows:
                cell_lines: list[str] = []
                for r_idx, row in enumerate(rows):
                    for c_idx, cell in enumerate(row):
                        cell_lines.append(f"<!-- block:{block_id}_r{r_idx}_c{c_idx} -->{cell}")
                parts.append("\n" + "\n".join(cell_lines))
            else:
                parts.append(f"\n<!-- block:{block_id} -->\n{txt}")
        else:
            parts.append(f"\n<!-- block:{block_id} -->\n{txt}")
    body = "\n".join(parts).strip()
    return (body + "\n") if body.strip() != f"# {title}" else None
