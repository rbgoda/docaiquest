"""Scoped reprocessing — propagate a root-cause fix to the docs that need it.

When a parsing/extraction defect is fixed in code (e.g. the multi-column PDF
reading-order fix that stopped HSC↔SSC scrambling), the fix only reaches a
document the next time it's re-ingested. This service finds EVERY document —
across all owners — that still exhibits the fixed symptom, so an operator can
re-run the pipeline on just that set instead of a blind, expensive, whole-corpus
overwrite.

Cross-owner by design → superadmin surface only (see routers/superadmin.py).
`scan()` is read-only and returns per-owner counts + a sample; `run()` enqueues a
full re-ingest (re-parse → chunk → embed → extract) for the reviewed pks. Only
docs with a stored source blob (`s3_key`) can be re-ingested, so scan filters to
those — a doc whose bytes we no longer hold is skipped, never silently failed.
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.orm import Document, DocumentChunk, User

log = logging.getLogger("docaiq.reprocess")

# Cap the accurate (byte-re-parsing) detector so a scan can never runaway-read the
# whole corpus from object storage in one request.
_MULTI_COLUMN_CAP = 800


def _largest_cell(md: str) -> int:
    """Longest cell in a Markdown table chunk — the two-column-layout-as-table
    artefact has a paragraph-sized cell; real data tables have short cells."""
    longest = 0
    for line in (md or "").splitlines():
        s = line.strip()
        if not s.startswith("|"):
            continue
        for cell in s.split("|"):
            longest = max(longest, len(cell.strip()))
    return longest


def _detect_layout_table(db: Session, tenant_id: str, doc_type: str | None) -> dict[int, str]:
    """CHEAP · docs with a table chunk whose largest cell is paragraph-sized — the
    exact artefact the ingestion table-guard now rejects. Pure SQL + string check,
    no byte reads."""
    rows = db.execute(
        select(DocumentChunk.document_pk, DocumentChunk.text).where(
            DocumentChunk.tenant_id == tenant_id, DocumentChunk.kind == "table")
    ).all()
    hits: dict[int, str] = {}
    for pk, text in rows:
        if _largest_cell(text) > 300:
            hits[pk] = "table chunk with a paragraph-sized cell (multi-column layout parsed as a table)"
    return hits


def _detect_multi_column(db: Session, tenant_id: str, doc_type: str | None) -> dict[int, str]:
    """ACCURATE · re-parse each ready PDF and flag those where the column detector
    finds a clean two-column gutter — precisely the docs the reading-order fix
    changes. Reads source bytes, so bounded by _MULTI_COLUMN_CAP."""
    import fitz

    from app import ingestion, storage
    q = select(Document.pk, Document.s3_key).where(
        Document.tenant_id == tenant_id,
        Document.mime_type == "application/pdf",
        Document.s3_key.isnot(None),
        Document.ingestion_status == "ready",
    )
    if doc_type:
        q = q.where(Document.doc_type == doc_type)
    docs = db.execute(q.limit(_MULTI_COLUMN_CAP)).all()
    hits: dict[int, str] = {}
    for pk, s3_key in docs:
        try:
            data = b"".join(storage.stream_object(s3_key))
            with fitz.open(stream=data, filetype="pdf") as d:
                for page in d:
                    if ingestion._page_to_blocks_columnar(page, 1):
                        hits[pk] = "multi-column PDF — reading order was interleaved"
                        break
        except Exception as e:  # noqa: BLE001 — one unreadable doc must not abort the scan
            log.debug("reprocess multi_column: pk=%s unreadable (%s)", pk, e)
    return hits


def _detect_doc_type(db: Session, tenant_id: str, doc_type: str | None) -> dict[int, str]:
    """BROAD · every ready doc of a given type — for re-extracting a whole type after
    a schema or prompt fix."""
    if not doc_type:
        return {}
    rows = db.execute(
        select(Document.pk).where(
            Document.tenant_id == tenant_id,
            Document.doc_type == doc_type,
            Document.ingestion_status == "ready",
        )
    ).all()
    return {pk: f"doc_type = {doc_type}" for (pk,) in rows}


# name → (label, detector, needs_doc_type). Order = display order.
SYMPTOMS: dict[str, tuple] = {
    "layout_table": ("Table chunk from a multi-column layout (fast)", _detect_layout_table, False),
    "multi_column_pdf": ("Multi-column PDF, interleaved reading order (re-parses bytes)", _detect_multi_column, False),
    "doc_type": ("All documents of a given type", _detect_doc_type, True),
}


def list_symptoms() -> list[dict]:
    return [{"key": k, "label": v[0], "needsDocType": v[2]} for k, v in SYMPTOMS.items()]


def scan(db: Session, tenant_id: str, symptom: str, doc_type: str | None = None,
         sample: int = 50) -> dict:
    """Read-only. Return the candidate documents matching `symptom`, grouped by
    owner, with a bounded sample for review. Only re-ingestable docs (stored blob)
    are returned."""
    if symptom not in SYMPTOMS:
        raise ValueError(f"unknown symptom '{symptom}'")
    _label, detector, needs_type = SYMPTOMS[symptom]
    if needs_type and not doc_type:
        raise ValueError("this symptom requires a doc_type")

    hits = detector(db, tenant_id, doc_type)
    if not hits:
        return {"symptom": symptom, "docType": doc_type, "total": 0, "byOwner": [], "sample": []}

    # Join owner + name, and drop any doc without a source blob (can't re-ingest).
    rows = db.execute(
        select(Document.pk, Document.owner_user_id, Document.name, Document.doc_type,
               Document.s3_key, User.email)
        .join(User, User.pk == Document.owner_user_id, isouter=True)
        .where(Document.pk.in_(list(hits.keys())))
    ).all()

    per_owner: dict[int, dict] = {}
    sample_rows: list[dict] = []
    total = 0
    for pk, owner, name, dtype, s3_key, email in rows:
        if not s3_key:
            continue  # no bytes to re-parse → not a candidate
        total += 1
        o = per_owner.setdefault(owner or 0, {"ownerPk": owner, "ownerEmail": email, "count": 0})
        o["count"] += 1
        if len(sample_rows) < sample:
            sample_rows.append({
                "pk": pk, "ownerPk": owner, "ownerEmail": email,
                "name": name, "docType": dtype, "reason": hits.get(pk),
            })

    by_owner = sorted(per_owner.values(), key=lambda x: x["count"], reverse=True)
    return {"symptom": symptom, "docType": doc_type, "total": total,
            "byOwner": by_owner, "sample": sample_rows}


async def run(db: Session, tenant_id: str, symptom: str | None = None,
              doc_type: str | None = None, doc_pks: list[int] | None = None,
              limit: int = 500) -> dict:
    """Enqueue a full re-ingest for the target docs. Provide an explicit `doc_pks`
    list, or a `symptom` (+ optional `doc_type`) to re-resolve the candidate set
    server-side. Bounded by `limit` so one call can't flood the queue; the response
    reports how many were enqueued vs skipped."""
    from app.queue import enqueue_ingest

    if doc_pks is None:
        if not symptom:
            raise ValueError("provide doc_pks or a symptom")
        scanned = scan(db, tenant_id, symptom, doc_type, sample=10_000)
        doc_pks = [r["pk"] for r in scanned["sample"]]

    # Re-validate: same tenant + has a source blob. Never enqueue across tenants.
    valid = db.execute(
        select(Document.pk).where(
            Document.pk.in_(doc_pks),
            Document.tenant_id == tenant_id,
            Document.s3_key.isnot(None),
        )
    ).all()
    valid_pks = [pk for (pk,) in valid][:limit]

    enqueued = 0
    for pk in valid_pks:
        try:
            await enqueue_ingest(pk, tenant_id)
            enqueued += 1
        except Exception as e:  # noqa: BLE001
            log.warning("reprocess run: enqueue failed for pk=%s: %s", pk, e)
    log.info("reprocess run: symptom=%s type=%s enqueued %d/%d docs (tenant=%s)",
             symptom, doc_type, enqueued, len(doc_pks), tenant_id)
    return {"requested": len(doc_pks), "enqueued": enqueued,
            "skipped": len(doc_pks) - enqueued, "cappedAt": limit}


# ── block_map one-shot population ────────────────────────────────────────────
# Migration 0107 added the block_map JSONB column, but existing docs need block
# geometry to populate it.  We extract text blocks directly from PDF pages via
# PyMuPDF (fitz) — no OCR, no Docling, no ML models.  Milliseconds per page.
# For non-PDF formats (images, DOCX, XLSX, Markdown, …) we build block_map from
# the existing DocumentChunk rows — each chunk becomes a block with synthetic
# full-page bbox so the Blocks view + per-block editing still work.


def _build_block_map_from_chunks(db: Session, doc_pk: int) -> dict[str, dict] | None:
    """Build a block_map from existing DocumentChunk rows for a non-PDF doc.

    Each chunk becomes one block with a synthetic full-page bbox.  No geometry
    information is lost (non-PDF docs never had page-level bboxes), but the
    Blocks view + per-block inline editing still work.

    Returns None when the doc has zero chunks."""
    from app.orm import DocumentChunk

    chunks = db.execute(
        select(DocumentChunk.kind, DocumentChunk.page, DocumentChunk.text, DocumentChunk.bbox)
        .where(DocumentChunk.document_pk == doc_pk)
        .order_by(DocumentChunk.page, DocumentChunk.chunk_index)
    ).all()

    if not chunks:
        return None

    reg: dict[str, dict] = {}
    for idx, (kind, page, text, cbbox) in enumerate(chunks):
        txt = (text or "").strip().replace(chr(0), "")
        if not txt:
            continue
        page_num = page or 1
        if cbbox and isinstance(cbbox, dict):
            pw = float(cbbox.get("page_w", 0) or cbbox.get("width", 0) or 1200)
            ph = float(cbbox.get("page_h", 0) or cbbox.get("height", 0) or 1600)
            x0 = float(cbbox.get("x0", 0))
            y0 = float(cbbox.get("y0", 0))
            x1 = float(cbbox.get("x1", pw))
            y1 = float(cbbox.get("y1", ph))
        else:
            # Synthetic full-page bbox for non-PDF docs
            pw, ph = 1200.0, 1600.0
            x0, y0, x1, y1 = 0.0, 0.0, pw, ph
        reg[f"b_{idx:04d}"] = {
            "kind": kind or "text",
            "page": page_num,
            "x0_pct": round(x0 / pw, 6) if pw > 0 else 0.0,
            "y0_pct": round(y0 / ph, 6) if ph > 0 else 0.0,
            "x1_pct": round(x1 / pw, 6) if pw > 0 else 1.0,
            "y1_pct": round(y1 / ph, 6) if ph > 0 else 1.0,
            "page_w": round(pw, 1),
            "page_h": round(ph, 1),
            "text": txt[:500],
        }
    return reg if reg else None


def _build_block_map_pymupdf(raw_bytes: bytes) -> dict[str, dict] | None:
    """Extract text blocks from a PDF via PyMuPDF and build a block_map registry.

    Each block gets a stable ID (b_0000…) with bbox as % of page dimensions.
    Returns None when no blocks with bboxes are found."""
    import fitz

    reg: dict[str, dict] = {}
    blk_idx = 0
    try:
        with fitz.open(stream=raw_bytes, filetype="pdf") as doc:
            for page_num in range(min(doc.page_count, 500)):
                page = doc[page_num]
                pw = page.rect.width
                ph = page.rect.height
                if pw <= 0 or ph <= 0:
                    continue
                blocks = page.get_text("blocks")
                for b in blocks:
                    # PyMuPDF block: (x0, y0, x1, y1, text, block_no, block_type)
                    if len(b) < 5:
                        continue
                    x0, y0, x1, y1 = float(b[0]), float(b[1]), float(b[2]), float(b[3])
                    text = (b[4] or "").strip()
                    # Sanitize: strip null bytes + other chars PostgreSQL JSONB rejects
                    text = text.replace(chr(0), "")  # strip null bytes for JSONB
                    if not text:
                        continue
                    # Drop blocks thinner than 1% of page (noise, footers, edges)
                    if (x1 - x0) / pw < 0.01 or (y1 - y0) / ph < 0.005:
                        continue
                    kind = "text" if len(b) < 7 else (
                        {0: "text", 1: "image"}.get(int(b[6]), "text"))
                    reg[f"b_{blk_idx:04d}"] = {
                        "kind": kind,
                        "page": page_num + 1,
                        "x0_pct": round(x0 / pw, 6),
                        "y0_pct": round(y0 / ph, 6),
                        "x1_pct": round(x1 / pw, 6),
                        "y1_pct": round(y1 / ph, 6),
                        "page_w": round(pw, 1),
                        "page_h": round(ph, 1),
                        "text": text[:500],
                    }
                    blk_idx += 1
    except Exception as e:
        log.warning("_build_block_map_pymupdf: %s", e)
        return None
    return reg if reg else None


def populate_block_maps(db: Session, tenant_id: str, dry_run: bool = False) -> dict:
    """Build block_map for every doc that has a source blob but no block_map.
    PDFs → PyMuPDF text-block extraction (fast, real bboxes).
    Non-PDFs → block_map from existing DocumentChunk rows (synthetic full-page bbox)."""
    from app import storage
    from app.orm import Document
    from sqlalchemy.orm import attributes

    docs = db.execute(
        select(Document).where(
            Document.tenant_id == tenant_id,
            Document.s3_key.isnot(None),
            Document.block_map.is_(None),
        )
    ).scalars().all()

    total = len(docs)
    updated, skipped, errors = 0, 0, 0
    by_format: dict[str, int] = {}

    for i, doc in enumerate(docs):
        name_lower = (doc.name or "").lower()
        mime = doc.mime_type or ""
        is_pdf = mime == "application/pdf" or name_lower.endswith(".pdf")

        # Derive a compact format label for the response
        if is_pdf:
            fmt = "pdf"
        else:
            ext = name_lower.rsplit(".", 1)[-1] if "." in name_lower else ""
            fmt = ext or (mime.split("/")[-1] if "/" in mime else "unknown")

        if is_pdf:
            # Read source bytes
            try:
                raw_bytes = b"".join(storage.stream_object(doc.s3_key))
            except Exception as e:
                log.warning("populate_block_maps [%d/%d] pk=%s · can't read s3: %s",
                            i + 1, total, doc.pk, e)
                errors += 1
                continue

            # Build block_map directly from PyMuPDF (no ML/OCR)
            bm = _build_block_map_pymupdf(raw_bytes)
            if not bm:
                log.info("populate_block_maps [%d/%d] pk=%s (%s) · PDF no blocks with bbox",
                         i + 1, total, doc.pk, doc.name or doc.id_external)
                skipped += 1
                by_format.setdefault(fmt, 0)
                by_format[fmt] += 1
                continue
        else:
            # Non-PDF: build block_map from existing DocumentChunk rows
            bm = _build_block_map_from_chunks(db, doc.pk)
            if not bm:
                log.info("populate_block_maps [%d/%d] pk=%s (%s) · no chunks to build blocks from",
                         i + 1, total, doc.pk, doc.name or doc.id_external)
                skipped += 1
                by_format.setdefault(fmt, 0)
                by_format[fmt] += 1
                continue

        if dry_run:
            log.info("populate_block_maps [%d/%d] pk=%s (%s) · WOULD write %d blocks",
                     i + 1, total, doc.pk, doc.name or doc.id_external, len(bm))
        else:
            doc.block_map = bm
            attributes.flag_modified(doc, "block_map")
            db.commit()
            log.info("populate_block_maps [%d/%d] pk=%s (%s) · wrote %d blocks",
                     i + 1, total, doc.pk, doc.name or doc.id_external, len(bm))
        updated += 1
        by_format[fmt] = by_format.get(fmt, 0) + 1

    return {
        "total": total, "updated": updated, "skipped": skipped, "errors": errors,
        "byFormat": by_format, "dryRun": dry_run,
    }
