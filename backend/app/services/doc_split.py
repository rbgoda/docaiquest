"""G9 · multi-document split (detect-then-confirm).

A single uploaded PDF sometimes bundles several distinct documents (a scan batch:
invoice + receipt + certificate). This service:

  · suggest_segments(pdf_bytes) — heuristic, read-only. Finds likely document
    boundaries and returns candidate segments. NEVER mutates anything.
  · apply_split(db, parent, segments, user) — explicit, confirm-gated. Extracts
    each page range into its own PDF, creates a child Document, and enqueues
    ingestion. NON-DESTRUCTIVE: the parent is kept (the caller can archive it).

Split is destructive-by-nature (1 → N), so the contract is detect-then-confirm:
the UI shows suggestions, the user approves ranges, only then does apply run.
"""
from __future__ import annotations

import io
import logging
import re
import secrets

import fitz  # PyMuPDF

from app.db import get_current_tenant
from app.repositories import documents as repo

log = logging.getLogger("docaiq.doc_split")

# Strong "this page starts a new document" signals, matched near the page top.
_START_KEYWORDS = re.compile(
    r"\b(invoice|tax invoice|receipt|statement|certificate|agreement|contract|"
    r"policy|report|purchase order|quotation|quote|payslip|pay slip|remittance|"
    r"bill of lading|credit note|delivery order|memorandum)\b", re.I)
# "Page 1 of N" / "1 / N" reset → a fresh document's first page.
_PAGE_ONE = re.compile(r"\b(?:page\s*)?1\s*(?:of|/)\s*\d+\b", re.I)


def _page_top(text: str, n: int = 300) -> str:
    return " ".join((text or "").split())[:n]


def _starts_new_doc(top: str, is_first: bool) -> bool:
    if is_first:
        return True
    return bool(_PAGE_ONE.search(top) or _START_KEYWORDS.search(top))


def suggest_segments(pdf_bytes: bytes) -> list[dict]:
    """Return candidate segments [{start_page, end_page, pages, title_hint}] (1-based,
    inclusive). Returns a single whole-doc segment when no internal boundary is
    found (i.e. 'no split suggested'). Best-effort + read-only."""
    segs: list[dict] = []
    try:
        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            n = doc.page_count
            if n <= 1:
                return [{"start_page": 1, "end_page": max(1, n), "pages": max(1, n), "title_hint": ""}]
            boundaries: list[int] = []
            tops: list[str] = []
            for i in range(n):
                top = _page_top(doc.load_page(i).get_text("text"))
                tops.append(top)
                if _starts_new_doc(top, i == 0):
                    boundaries.append(i)
            # Always start at page 0; dedupe + sort.
            boundaries = sorted(set([0, *boundaries]))
            for bi, start in enumerate(boundaries):
                end = (boundaries[bi + 1] - 1) if bi + 1 < len(boundaries) else (n - 1)
                # first non-empty line of the segment's first page as a title hint
                hint = next((ln.strip() for ln in (tops[start] or "").split("  ") if ln.strip()), "")[:80]
                segs.append({
                    "start_page": start + 1, "end_page": end + 1,
                    "pages": end - start + 1, "title_hint": hint,
                })
    except Exception as e:  # noqa: BLE001
        log.warning("split suggest failed (non-fatal): %s", e)
        return []
    return segs


def apply_split(db, parent, segments: list[dict], *, uploaded_by: str) -> list[dict]:
    """Create one child Document per confirmed segment from the parent's PDF.
    `segments` = [{start_page, end_page, name?}] (1-based inclusive). Returns the
    created child doc dicts. NON-DESTRUCTIVE — parent untouched."""
    import hashlib

    from app import storage
    tenant = get_current_tenant()
    blob = storage.get_object_bytes(parent.s3_key) if parent.s3_key else None
    if not blob:
        raise ValueError("parent document has no stored file to split")

    created: list[dict] = []
    with fitz.open(stream=blob, filetype="pdf") as src:
        npages = src.page_count
        for seg in segments:
            a = int(seg.get("start_page", 1))
            b = int(seg.get("end_page", a))
            if a < 1 or b < a or b > npages:
                log.warning("split: skip invalid range %s-%s (doc has %s pages)", a, b, npages)
                continue
            sub = fitz.open()
            sub.insert_pdf(src, from_page=a - 1, to_page=b - 1)
            child_bytes = sub.tobytes()
            sub.close()
            sha = hashlib.sha256(child_bytes).hexdigest()
            suffix = secrets.token_hex(8)
            s3_key = f"{tenant}/documents/{sha[:2]}/{sha}-{suffix}"
            storage.put_object(s3_key, io.BytesIO(child_bytes), content_type="application/pdf")
            base = (parent.name or "document").rsplit(".", 1)[0]
            name = (seg.get("name") or f"{base} — part {a}-{b}.pdf")[:256]
            row = repo.create_upload(
                db,
                id_external=f"doc-up-{sha[:10]}-{secrets.token_hex(3)}",
                name=name, path=f"Split of {parent.id_external}",
                size=_human_size(len(child_bytes)), pages=(b - a + 1),
                mime_type="application/pdf", sha256=sha, s3_key=s3_key,
                uploaded_by=uploaded_by, source="split", source_ref=parent.id_external,
            )
            row.ingestion_status = "pending"
            db.commit()
            created.append({"id_external": row.id_external, "pk": row.pk,
                            "name": row.name, "pages": row.pages,
                            "dict": repo._to_dict(row)})  # noqa: SLF001
    return created


def _human_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{int(n)} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"
