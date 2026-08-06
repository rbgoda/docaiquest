"""M44.P4 · Document artifact materializer.

Runs ONCE per document, after ingestion finishes. Generates the persistent
artifacts (markdown / summary / structured JSON / entities / TOC) that
all later requests serve from DB without re-running the LLM.

Strategy gating
---------------
Size of the doc decides what artifacts get generated:

  ≤ 20 pages  / ≤ 80K chars   → full          (markdown + summary + JSON + entities + TOC)
  21-50 pages / ≤ 200K chars  → reduced       (markdown + summary + entities, no JSON)
  51-150 pages / ≤ 600K chars → summary_only  (summary + TOC + entities)
  > 150 pages / > 600K chars  → skipped       (summary_short only · large-doc banner)

The thresholds matter because the LLM calls per artifact aren't free.
For a 200-page doc, generating full markdown via multi-pass is ~25 LLM
calls minimum. We gate hard to avoid burning tokens on artifacts the
reviewer will never read in full anyway.

What we DON'T do
----------------
This job NEVER raises. Every artifact is wrapped in try/except — one
failed artifact (e.g. structured_json fails on a poorly-classified doc)
doesn't sink the whole materialization. The doc still gets the other
artifacts and a `processing_notes` string explaining what was skipped.
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import SessionLocal, set_current_tenant
from app.orm import Document, DocumentArtifact, DocumentChunk

log = logging.getLogger("docaiq.materializer")

# ── Strategy thresholds (tunable via settings if needed) ──────────────────
# Realized after the first 51-page doc test that page count was too tight
# a gate · 51 pages with 65K chars is much smaller than 30 pages with 75K
# chars. Char count is the real LLM-cost driver. The page caps now serve
# as safety nets against extreme corner cases (e.g. a 200-page doc that
# happens to be mostly blank pages reads small but might OOM the LLM).
_FULL_MAX_PAGES = 30
_FULL_MAX_CHARS = 80_000
_REDUCED_MAX_PAGES = 100
_REDUCED_MAX_CHARS = 200_000
_SUMMARY_ONLY_MAX_PAGES = 300
_SUMMARY_ONLY_MAX_CHARS = 600_000

# Markdown multi-pass tuning
_MD_WINDOW_CHARS = 8_000          # per pass
_MD_MAX_OUTPUT_TOKENS = 4_000     # per pass
_MD_MAX_WINDOWS_FULL = 8          # ≤ 64K chars · ~30 dense pages
_MD_MAX_WINDOWS_REDUCED = 25      # ≤ 200K chars · ~50 dense pages


def materialize_for_document(db: Session, doc_pk: int, tenant_id: str) -> dict:
    """Synchronous entry · runs everything inline. Returns the artifact
    row's contents as a dict so the worker can log the strategy chosen.
    NEVER raises — failure stays in processing_notes."""
    set_current_tenant(tenant_id)

    doc = db.scalar(select(Document).where(
        Document.pk == doc_pk,
        Document.tenant_id == tenant_id,
    ))
    if doc is None:
        log.warning("materialize: doc pk=%d not found in tenant %s", doc_pk, tenant_id)
        return {"error": "doc not found"}

    # Re-materialize · drop any existing row first so this is idempotent.
    existing = db.scalar(select(DocumentArtifact).where(DocumentArtifact.document_pk == doc.pk))
    if existing is not None:
        db.delete(existing)
        db.flush()

    # Assemble the doc text from chunks (ordered by chunk_index).
    chunks = db.scalars(
        select(DocumentChunk)
        .where(DocumentChunk.document_pk == doc.pk)
        .order_by(DocumentChunk.chunk_index)
    ).all()
    # The LLM summary pass runs over the flowing TEXT chunks. (Table chunks are
    # not needed here since M51 made markdown lazy — generated on first view.)
    text_chunks = [c for c in chunks if (c.kind or "text") != "table"]
    full_text = "\n\n".join((c.text or "") for c in text_chunks).strip()

    char_count = len(full_text)
    page_count = doc.pages or max((c.page for c in chunks), default=1) or 1
    strategy = _pick_strategy(char_count=char_count, page_count=page_count)

    log.info(
        "materialize doc_pk=%d tenant=%s · %d pages · %d chars · strategy=%s",
        doc.pk, tenant_id, page_count, char_count, strategy,
    )

    notes: list[str] = [f"Strategy: {strategy}. {page_count} pages · {char_count} chars."]
    artifact = DocumentArtifact(
        tenant_id=tenant_id,
        document_pk=doc.pk,
        processing_strategy=strategy,
        page_count=page_count,
        char_count=char_count,
        token_count=char_count // 4,  # rough · 4 chars per token
    )

    if strategy == "skipped":
        artifact.summary_short = _safe(
            _gen_summary_short, full_text, log_label="summary_short",
            notes=notes, fallback="(document too large for automatic summarization)",
        )
        artifact.processing_notes = " · ".join(notes)
        db.add(artifact)
        db.commit()
        return _to_dict(artifact)

    # summary_long is generated for every non-skipped tier — it's the
    # single most-useful artifact and the cheapest (one LLM call).
    artifact.summary_short = _safe(_gen_summary_short, full_text, log_label="summary_short", notes=notes)
    artifact.summary_long = _safe(_gen_summary_long, full_text, log_label="summary_long", notes=notes)

    # Entities are cheap · just query the entities table that was
    # populated during ingestion. Group by kind, dedupe, return top-N.
    artifact.key_entities = _safe(
        lambda: _gen_key_entities(db, doc.pk, tenant_id),
        log_label="key_entities", notes=notes, fallback=[],
    )

    # TOC · heuristic + LLM fallback. Always cheap enough to include
    # for non-skipped tiers.
    artifact.table_of_contents = _safe(
        lambda: _gen_table_of_contents(full_text), log_label="toc", notes=notes, fallback=[],
    )

    # M51 · markdown is now generated LAZILY on first Markdown-tab open. It's the
    # most expensive artifact (multi-pass: up to 8 LLM calls on 'full', 25 on
    # 'reduced'), and most uploads are never opened in the Markdown tab — so we
    # defer it. The chat full-doc path falls back to the raw-text excerpt, and
    # the /markdown endpoint generates + CACHES on demand. full_text_md stays
    # NULL here for full/reduced tiers.
    if strategy in ("full", "reduced"):
        notes.append("Markdown deferred (lazy · generated + cached on first view).")

    # M51 · structured_json materialization REMOVED. It was a 2000-token LLM
    # call whose ONLY consumer was the JSON tab, and it only ran for the "full"
    # tier (so it failed on most multi-page docs). The authoritative role-aware
    # `documents.extracted_fields` already covers chat, the extraction API,
    # analytics, and the JSON tab — for free. Dropping it saves the most
    # expensive per-doc artifact; the column stays for backward-compat.

    artifact.processing_notes = " · ".join(notes)
    db.add(artifact)
    db.commit()
    return _to_dict(artifact)


# ── Strategy selection ────────────────────────────────────────────────────
def _pick_strategy(*, char_count: int, page_count: int) -> str:
    if char_count <= _FULL_MAX_CHARS and page_count <= _FULL_MAX_PAGES:
        return "full"
    if char_count <= _REDUCED_MAX_CHARS and page_count <= _REDUCED_MAX_PAGES:
        return "reduced"
    if char_count <= _SUMMARY_ONLY_MAX_CHARS and page_count <= _SUMMARY_ONLY_MAX_PAGES:
        return "summary_only"
    return "skipped"


# ── Generators · each callable returns its artifact or raises ─────────────

def _gen_summary_short(full_text: str) -> str:
    """2-3 sentence orientation · one cheap LLM call."""
    from app.services.doc_chat import llm_one_shot
    from app.db import SessionLocal as _SL
    db = _SL()
    try:
        system = (
            "Write a 2-3 sentence summary of the document below. State the "
            "document type, the main parties or subject, and the key fact "
            "a reviewer needs first. No preamble, no markdown — just the "
            "summary as plain prose."
        )
        return llm_one_shot(db, system, full_text[:30_000], max_tokens=180).strip()
    finally:
        db.close()


def _gen_summary_long(full_text: str) -> str:
    """In-depth, structured analysis · one LLM call. This is now the highest-
    value artifact (we dropped the redundant structured_json), so it's worth a
    fuller token budget — a reviewer should understand the doc without opening
    the original."""
    from app.services.doc_chat import llm_one_shot
    from app.db import SessionLocal as _SL
    db = _SL()
    try:
        system = (
            "You are a meticulous document analyst. Write an IN-DEPTH, well-"
            "structured summary of the document below so a reviewer understands "
            "it fully without reading the original. Use short markdown headings "
            "(`##`) and cover, where applicable:\n"
            "  · **Overview** — what this document is, its purpose, who issued it\n"
            "  · **Parties / subjects** — every named person/org WITH their ROLE "
            "(applicant, beneficiary, issuer, emergency contact, signatory, …)\n"
            "  · **Key dates** — issue / effective / expiry / due, and what each governs\n"
            "  · **Amounts / IDs / references** — quote every figure, account, "
            "policy / ID number verbatim with its label\n"
            "  · **Key terms & obligations** — coverage, conditions, "
            "responsibilities, limits, exclusions\n"
            "  · **Notable / watch-outs** — anything unusual, missing, expiring "
            "soon, or that a reviewer should verify\n"
            "Be specific and quote exact values. Never invent. Omit a section "
            "only if the document genuinely has nothing for it."
        )
        return llm_one_shot(db, system, full_text[:50_000], max_tokens=1200).strip()
    finally:
        db.close()


def _gen_markdown(full_text: str, *, max_windows: int) -> str:
    """Multi-pass markdown generation. Each pass converts a window of
    raw text into clean markdown; results concatenate with `---` separators
    so structure stays readable."""
    from app.services.doc_chat import llm_one_shot
    from app.db import SessionLocal as _SL
    db = _SL()
    try:
        system = (
            "Convert the document text below into clean GitHub-flavoured "
            "Markdown. Preserve all values, headings, tables, line items. "
            "Use `##` for top-level sections, `###` for sub-sections. "
            "Render tables as Markdown tables. Do NOT summarize or skip "
            "content. Do NOT add commentary."
        )
        windows = _split_into_windows(full_text, _MD_WINDOW_CHARS, max_windows)
        out: list[str] = []
        for i, w in enumerate(windows, start=1):
            chunk_md = llm_one_shot(
                db, system,
                f"[Window {i}/{len(windows)}]\n\n{w}",
                max_tokens=_MD_MAX_OUTPUT_TOKENS,
            ).strip()
            if chunk_md:
                out.append(chunk_md)
        if len(_split_into_windows(full_text, _MD_WINDOW_CHARS, max_windows + 100)) > max_windows:
            out.append(
                f"\n\n---\n\n_[Remainder of the document truncated — exceeded "
                f"the {max_windows}-window cap. View the original PDF for the "
                f"full text.]_"
            )
        return "\n\n---\n\n".join(out)
    finally:
        db.close()


def _gen_structured_json(full_text: str) -> dict | None:
    """Single LLM call producing typed JSON. Best for small docs · for
    larger ones the call truncates anyway and we get only partial data."""
    from app.services.doc_chat import llm_one_shot
    from app.db import SessionLocal as _SL
    db = _SL()
    try:
        system = (
            "Extract every typed field from the document below into JSON. "
            "Use snake_case keys. Group repeated structures into arrays "
            "(e.g. line_items, parties, signatures). For values you can't "
            "confidently extract, omit the key entirely — do NOT invent. "
            "Reply with ONLY the JSON object."
        )
        raw = llm_one_shot(db, system, full_text[:50_000], max_tokens=2000).strip()
        if raw.startswith("```"):
            # Strip code fences if model added them
            lines = raw.split("\n")
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip().startswith("```"):
                lines = lines[:-1]
            raw = "\n".join(lines).strip()
        import json as _json
        return _json.loads(raw) if raw else None
    except Exception:  # noqa: BLE001
        return None
    finally:
        db.close()


def _gen_key_entities(db: Session, doc_pk: int, tenant_id: str) -> list[dict]:
    """Query the entities table (already populated during ingest) and
    group by kind. Returns the top 20 most-frequent entities per doc."""
    from app.orm import Entity
    rows = db.scalars(
        select(Entity)
        .where(Entity.tenant_id == tenant_id, Entity.document_pk == doc_pk)
        .order_by(Entity.kind, Entity.page)
    ).all()
    seen: set[tuple[str, str]] = set()
    out: list[dict] = []
    for r in rows:
        key = (r.kind, (r.text or "").strip().lower())
        if not key[1] or key in seen:
            continue
        seen.add(key)
        out.append({
            "kind": r.kind,
            "text": r.text,
            "page": int(r.page),
        })
        if len(out) >= 30:
            break
    return out


def _gen_table_of_contents(full_text: str) -> list[dict]:
    """Heuristic TOC from heading patterns. Recognises numbered sections,
    'Section N · Title', 'CHAPTER N', etc. Returns ordered list."""
    import re
    lines = full_text.split("\n")
    toc: list[dict] = []
    seen: set[str] = set()
    # Patterns ordered by specificity
    patterns = [
        (r"^\s*(?:section|chapter|article|part)\s+\d+[:\.\s]\s*(.+?)\s*$", "section"),
        (r"^\s*\d+[\.\)]\s+([A-Z][A-Z\s/]{4,}?)\s*$", "heading"),  # all-caps headings
        (r"^\s*([A-Z][A-Z\s/&]{5,80}?)\s*$", "heading"),
    ]
    for line in lines[:2000]:  # cap to keep this fast
        for pat, kind in patterns:
            m = re.match(pat, line, re.IGNORECASE)
            if m:
                title = m.group(1).strip()[:120]
                if title and title.lower() not in seen and len(title) >= 5:
                    seen.add(title.lower())
                    toc.append({"title": title, "kind": kind})
                    if len(toc) >= 40:
                        return toc
                break
    return toc


# ── Plumbing helpers ──────────────────────────────────────────────────────
def _split_into_windows(text: str, window_chars: int, max_windows: int) -> list[str]:
    """Split on paragraph boundaries when possible, char-cut otherwise.
    Bounded by max_windows."""
    out: list[str] = []
    remaining = text
    while remaining and len(out) < max_windows:
        if len(remaining) <= window_chars:
            out.append(remaining)
            break
        # Try to break at a paragraph in the last 1K chars of the window
        cut = remaining.rfind("\n\n", window_chars - 1000, window_chars)
        if cut < 0:
            cut = window_chars
        out.append(remaining[:cut])
        remaining = remaining[cut:].lstrip()
    return out


def _safe(fn, *args, log_label: str, notes: list[str], fallback=None, **kwargs):
    """Run a generator. On failure, append to notes and return fallback.
    NEVER raises — protects the overall materialization run."""
    try:
        return fn(*args, **kwargs)
    except Exception as e:  # noqa: BLE001
        log.warning("materialize · %s failed: %s", log_label, e)
        notes.append(f"{log_label} skipped: {type(e).__name__}")
        return fallback


def _to_dict(a: DocumentArtifact) -> dict:
    return {
        "pk": a.pk,
        "documentPk": a.document_pk,
        "processingStrategy": a.processing_strategy,
        "processingNotes": a.processing_notes,
        "hasMarkdown": a.full_text_md is not None,
        "hasJson": a.structured_json is not None,
        "hasSummaryLong": a.summary_long is not None,
        "hasEntities": bool(a.key_entities),
        "hasToc": bool(a.table_of_contents),
        "charCount": a.char_count,
        "pageCount": a.page_count,
        "tokenCount": a.token_count,
    }


# ── Arq task wrapper ──────────────────────────────────────────────────────
async def materialize_artifacts_task(ctx: dict, document_pk: int, tenant_id: str) -> dict:
    """Arq entry. Runs synchronously inside the async task (the body is
    blocking-DB anyway; Arq's threadpool covers it). Returns a stats
    dict for the job result store."""
    db = SessionLocal()
    try:
        return materialize_for_document(db, document_pk, tenant_id)
    finally:
        db.close()


def run_now_for_doc(document_pk: int, tenant_id: str) -> dict:
    """Synchronous trigger for smoke tests / backfill."""
    db = SessionLocal()
    try:
        return materialize_for_document(db, document_pk, tenant_id)
    finally:
        db.close()
