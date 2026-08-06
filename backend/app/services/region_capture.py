"""Capture the text inside a user-drawn region (M53 · annotations).

Given a normalized 0..1 rectangle on a page, return the text it covers — native
PyMuPDF clip-text for text PDFs (free, exact), region-OCR fallback for scanned /
image pages (one small vision call on just the crop)."""
from __future__ import annotations

import logging

import fitz  # PyMuPDF

log = logging.getLogger("docaiq.region_capture")


def capture_region_text(pdf_bytes: bytes, page: int, x0: float, y0: float, x1: float, y1: float,
                        *, db=None, tenant_id: str | None = None) -> str:
    """Text inside the normalized (0..1) rect on `page` (1-based)."""
    if not pdf_bytes:
        return ""
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception:  # noqa: BLE001 — non-PDF (DOCX/etc.): no page geometry to clip
        return ""
    try:
        if page < 1 or page > doc.page_count:
            page = 1
        pg = doc[page - 1]
        W, H = float(pg.rect.width), float(pg.rect.height)
        ax0, ax1 = sorted((max(0.0, min(1.0, x0)), max(0.0, min(1.0, x1))))
        ay0, ay1 = sorted((max(0.0, min(1.0, y0)), max(0.0, min(1.0, y1))))
        clip = fitz.Rect(ax0 * W, ay0 * H, ax1 * W, ay1 * H)
        if clip.is_empty or clip.width < 1 or clip.height < 1:
            return ""
        native = (pg.get_text("text", clip=clip) or "").strip()
        if len(native) >= 3:
            return native
        # Scanned / no native text in the box → OCR just the crop.
        try:
            png = pg.get_pixmap(dpi=200, clip=clip).tobytes("png")
            from app.ingestion_vision import transcribe_page
            return (transcribe_page(png, db=db, tenant_id=tenant_id) or "").strip()
        except Exception as e:  # noqa: BLE001
            log.warning("region OCR failed (page %s): %s", page, e)
            return native
    finally:
        doc.close()
