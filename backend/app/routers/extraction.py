"""M49 · cross-app extraction API.

Lets the (separate) audit app reuse Documents' extraction intelligence to pull
structured evidence for its framework requirements — without forking the
pipeline. Wraps the SAME `classifier` + `fact_extractor.extract` the product
uses.

Stateless: the audit app POSTs a file; we parse → chunk → embed into a TEMP
document (tenant-scoped, never owner-visible), classify, extract, then delete the
temp doc. Nothing the audit app sends is retained.

Auth: a service API key in `X-API-Key` (DOCAIQ_EXTRACTION_API_KEY). Empty key =
endpoint disabled (401).
"""
from __future__ import annotations

import logging
import secrets

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.api_clients import require_client
from app.config import get_settings
from app.db import get_session, set_current_tenant
from app.feature_flags import is_enabled

router = APIRouter()
log = logging.getLogger("docaiq.extraction")

# M52 · auth flows through app.api_clients.require_client — per-partner keys
# (scoped, rate-limited) PLUS the legacy DOCAIQ_EXTRACTION_API_KEY as an implicit
# all-scope client for back-compat. See docs/SDK_AND_API_DESIGN.md §4.

_PARSERS = {
    "application/pdf": "parse_pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "parse_docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "parse_xlsx",
    "message/rfc822": "parse_eml",
}


def _sweep_orphan_temp_docs(db: Session, tenant_id: str) -> None:
    """Safety net: delete any source='api' temp docs left behind by a request
    that crashed before its inline cleanup. Cheap; runs once per extract call.
    Only touches docs older than an hour so it never races a concurrent extract."""
    try:
        import datetime as _dt
        from sqlalchemy import select
        from app.orm import Document as _Doc
        cutoff = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(hours=1)
        stale = db.scalars(select(_Doc).where(
            _Doc.tenant_id == tenant_id, _Doc.source == "api",
            _Doc.created_at < cutoff)).all()
        for d in stale:
            db.delete(d)
        if stale:
            db.commit()
            log.info("extraction: swept %d orphaned temp doc(s)", len(stale))
    except Exception as e:  # noqa: BLE001 — never fail an extract on the sweep
        db.rollback()
        log.warning("extraction: orphan sweep failed: %s", e)


def _parse(file_bytes: bytes, mime: str, name: str):
    """Bytes → [(page, text)] using the product's own parsers."""
    from app import ingestion
    fn = _PARSERS.get(mime)
    if fn is None:
        # default: PDFs by extension, else plain text
        if name.lower().endswith(".pdf"):
            fn = "parse_pdf"
        else:
            return ingestion.parse_text(file_bytes)
    return getattr(ingestion, fn)(file_bytes)


@router.post("/extract", dependencies=[Depends(require_client("extract"))])
async def extract_file(
    file: UploadFile = File(...),
    target_schema: str | None = Form(default=None),
    audit_framework: str | None = Form(default=None),
    audit_requirement_id: str | None = Form(default=None),
    db: Session = Depends(get_session),
) -> dict:
    """Extract structured fields + citations from an uploaded document. Reuses
    the product's classifier + fact_extractor on a throwaway temp document."""
    from app import ingestion
    from app.agents import classifier, fact_extractor
    from app.orm import Document, DocumentChunk

    settings = get_settings()
    set_current_tenant(settings.tenant_id)
    _sweep_orphan_temp_docs(db, settings.tenant_id)  # safety net for any prior crash
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="empty file")
    # DoS guard: cap the upload like the user-facing path (nginx also caps upstream).
    if len(raw) > settings.max_upload_bytes:
        raise HTTPException(status_code=413,
                            detail=f"file too large (> {settings.max_upload_bytes} bytes)")

    pages = _parse(raw, file.content_type or "", file.filename or "doc")
    chunks = ingestion.chunk_pages(pages)
    if not chunks:
        return {"status": "no_text", "fields": {}, "doc_type": None,
                "audit": {"framework": audit_framework, "requirementId": audit_requirement_id}}
    # Extraction reads chunk TEXT only (fact_extractor._build_text_excerpt); it never
    # runs vector retrieval on the temp doc — so skip the (paid) embedding pass and
    # store cheap zero-vectors just to satisfy the NOT NULL column.
    dim = getattr(settings, "embed_dim", 384)
    vectors = [[0.0] * dim for _ in chunks]

    import hashlib
    tid = settings.tenant_id
    fname = file.filename or "extract"
    doc = Document(
        tenant_id=tid, owner_user_id=None,
        id_external=f"extract-{secrets.token_hex(8)}",
        name=fname, source="api", ingestion_status="ready",
        # legacy NOT NULL columns — synthesize safe values for the temp doc
        path="api://extract", size=str(len(raw)), modified="", pages=len(pages) or 1,
        current_page=1, type="Uploaded", mime_type=(file.content_type or "application/octet-stream"),
        sha256=hashlib.sha256(raw).hexdigest(), uploaded_by="extraction-api",
        content="api-extract",  # legacy String(64) label, not the body (text lives in chunks)
    )
    db.add(doc)
    db.flush()  # → doc.pk
    try:
        for i, (c, v) in enumerate(zip(chunks, vectors)):
            db.add(DocumentChunk(
                tenant_id=tid, document_pk=doc.pk, chunk_index=i,
                text=c.text, kind=getattr(c, "kind", "text"), page=c.page,
                char_start=c.char_start, char_end=c.char_end, embedding=v))
        db.flush()

        # doc_type: caller override, else the product classifier
        doc_type = target_schema
        if not doc_type:
            excerpt = "\n".join(c.text for c in chunks)[:4000]
            cls = classifier.classify_text(excerpt)
            doc_type = cls.top.doc_type if cls else "unknown"

        result = fact_extractor.extract(db, document_pk=doc.pk, classifier_doc_type=doc_type)
        if result is None:
            return {"status": "no_extraction",
                    "reason": "no OpenRouter key or no schema match",
                    "doc_type": doc_type, "fields": {},
                    "audit": {"framework": audit_framework, "requirementId": audit_requirement_id}}
        payload = result.to_jsonb()
        return {
            "status": "extracted",
            "schemaUsed": result.schema_key,
            "docType": doc_type,
            "fields": payload.get("fields", {}),
            "confidence": payload.get("confidence"),
            "citations": payload.get("field_bboxes", {}),
            "warnings": payload.get("warnings", []),
            "model": payload.get("model"),
            "audit": {"framework": audit_framework, "requirementId": audit_requirement_id},
        }
    finally:
        # Never retain what the audit app sent — drop the temp doc + chunks.
        try:
            db.delete(doc)
            db.commit()
        except Exception as e:  # noqa: BLE001
            db.rollback()
            log.warning("extraction: temp-doc cleanup failed: %s", e)


@router.get("/health", dependencies=[Depends(require_client())])
def extraction_health() -> dict:
    s = get_settings()
    return {"status": "ok", "llmReady": bool(s.openrouter_api_key),
            "universal": is_enabled("documents_universal_extractor", True)}
