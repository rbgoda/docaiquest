"""Doc-scoped chat — M11.7. Conversation with the AI about a single
uploaded document. Reuses the existing validator agent + retrieval
pipeline (with doc_id_external filter), plus a doc-specific system
prompt tuned for "chat with this document" instead of "judge a
compliance requirement".

Endpoints (all admin/reviewer):
  GET  /api/documents/{doc_id}/chat          → thread + summary
  POST /api/documents/{doc_id}/chat/messages → new user message → AI reply with citations
  POST /api/documents/{doc_id}/summary       → generate or refresh the auto-summary
  POST /api/documents/{doc_id}/markdown      → convert doc to clean Markdown
  POST /api/documents/{doc_id}/json          → convert doc to structured JSON
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_session
from app.orm import Document, DocumentArtifact
# shared helpers stay in doc_chat (one-directional import — doc_chat never imports us)
from app.routers.doc_chat import ExportResponse, _load_doc, _reveal_fn
from app.security import CurrentUser, require_role
from app.services import doc_chat as doc_chat_service

log = logging.getLogger(__name__)

# Re-exports kept for backwards compatibility with any test or external
# code that imported the private helpers from here (TODO #25 conservative
# extraction — see services/doc_chat.py).
_FACTS_NOT_FOUND_SENTINEL = doc_chat_service.FACTS_NOT_FOUND_SENTINEL
_doc_text_excerpt = doc_chat_service.doc_text_excerpt
_llm_one_shot = doc_chat_service.llm_one_shot
_try_answer_from_facts = doc_chat_service.try_answer_from_facts

router = APIRouter()

# M46 · in-process cache for LLM-structured bodies. Keyed by (doc_pk, text len +
# cheap hash) so a re-extract (which changes the text) busts it naturally. Caps
# at 256 entries (FIFO) — this is a view-time convenience cache, not durable.
# Lives here with its sole user (_structure_body_md); it was orphaned in doc_chat.py
# when this function was extracted (commit dc78ff0), which left the references below
# undefined → a latent NameError on the Markdown export for artifact-less docs.
_BODY_MD_CACHE: "dict[tuple, str]" = {}

# The on-demand LLM markdown structurer only sees this many chars of the body — so it's
# both the model's input window AND the cutoff above which we skip it (a longer doc gets
# the instant, complete deterministic render instead; see export_markdown).
_MD_LLM_MAX_CHARS = 12000


def _structure_body_md(db: Session, doc: Document, text: str) -> str | None:
    """M46 · Convert the raw extracted document text into clean, fully-structured
    GitHub-flavoured Markdown via one LLM call (documents route at paid Haiku, so
    this is reliable). The text may be PII-tokenized — the model is told to
    preserve [BRACKETED_TOKENS] verbatim, and the caller re-applies _reveal_fn,
    so no cleartext PII ever reaches the model. Returns None on empty/failure so
    the caller falls back to the raw-text body."""
    body = (text or "").strip()
    if not body:
        return None
    key = (doc.pk, len(body), hash(body[:2000]) ^ hash(body[-2000:]))
    cached = _BODY_MD_CACHE.get(key)
    if cached is not None:
        return cached or None
    # One call over a generous window. Documents are routed at a paid tier, so a
    # single 12K-char pass is reliable; longer docs keep the structured head and
    # the tail is appended raw by the caller (rare for personal docs).
    system = (
        "You convert a document's raw extracted text into clean, well-structured "
        "GitHub-Flavoured Markdown. Rules:\n"
        "- Use #/##/### headings for sections, **bold** for emphasis, '- ' bullet "
        "and '1.' numbered lists, > blockquotes, and | tables | where the source "
        "is clearly tabular.\n"
        "- Preserve the document's reading order and ALL substantive content. Do "
        "NOT summarise, omit, or invent.\n"
        "- Strip page headers/footers, page numbers, and OCR/pagination artefacts.\n"
        "- Preserve any [BRACKETED_TOKENS] EXACTLY as written — they are redaction "
        "placeholders.\n"
        "- Output Markdown ONLY: no preamble, no commentary, no surrounding code fence."
    )
    try:
        out = (_llm_one_shot(db, system, body[:_MD_LLM_MAX_CHARS], max_tokens=2400,
                             cache_system=True) or "").strip()
    except Exception as e:  # noqa: BLE001 — any failure → deterministic fallback
        log.warning("markdown body-structure failed for doc %s: %s", doc.id_external, e)
        out = ""
    out = out.removeprefix("```markdown").removeprefix("```").removesuffix("```").strip()
    if len(_BODY_MD_CACHE) > 256:
        _BODY_MD_CACHE.pop(next(iter(_BODY_MD_CACHE)))
    _BODY_MD_CACHE[key] = out
    return out or None


def _deterministic_markdown(doc: Document, text: str, body_md: str | None = None) -> str:
    """M46 · Build clean Markdown from the structured extraction (incl. the
    records table) + raw text — with NO LLM call. Reliable + instant, so the
    Markdown tab never fails on a provider rate-limit. Used as the documents-
    product path and as the universal fallback when on-demand LLM markdown
    yields nothing."""
    ef = doc.extracted_fields or {}
    f = ef.get("fields") if isinstance(ef.get("fields"), dict) else ef
    out: list[str] = []

    title = (f.get("title") or doc.name or "Document").strip()
    out.append(f"# {title}")
    dt = f.get("detected_doc_type") or doc.doc_type
    if dt:
        out.append(f"*Type: {str(dt).replace('_', ' ')}*")
    notes = (ef.get("_notes") or f.get("summary") or "").strip()
    if notes:
        out.append(f"\n{notes}")

    def _esc(s) -> str:
        return str(s or "").replace("|", "\\|").replace("\n", " ").strip()

    # Scalar + list "key facts" sections.
    def _kv_section(heading: str, items, label_key="label", value_key="value"):
        rows = [i for i in (items or []) if isinstance(i, dict) and (i.get(value_key) or i.get("name"))]
        if not rows:
            return
        out.append(f"\n## {heading}\n")
        for i in rows:
            lab = i.get(label_key) or i.get("role") or ""
            val = i.get(value_key) or i.get("name") or ""
            out.append(f"- **{_esc(lab) or '—'}:** {_esc(val)}")

    _kv_section("Parties", f.get("parties"), label_key="role", value_key="name")
    _kv_section("Key dates", f.get("dates"))
    _kv_section("Amounts", f.get("amounts"))
    _kv_section("Identifiers", f.get("identifiers"))
    _kv_section("Key facts", f.get("key_facts"))

    # Records → a Markdown table (transactions / line items / results / …).
    recs = f.get("records") or []
    if recs:
        out.append("\n## Records\n")
        out.append("| Date | Description | Amount | Reference | Details |")
        out.append("|---|---|---|---|---|")
        for r in recs[:300]:
            if not isinstance(r, dict):
                continue
            attrs = "; ".join(
                f"{_esc(a.get('label'))}={_esc(a.get('value'))}"
                for a in (r.get("attributes") or []) if isinstance(a, dict) and a.get("value")
            )
            out.append(
                f"| {_esc(r.get('date'))} | {_esc(r.get('description'))} | "
                f"{_esc(r.get('amount'))} | {_esc(r.get('reference'))} | {attrs} |"
            )

    if body_md:
        # LLM-structured body — already clean GFM. No "Document text" wrapper so
        # the structured content reads as the document itself.
        out.append("\n" + body_md.strip())
    else:
        body = (text or "").strip()
        if body:
            out.append("\n## Document text\n")
            # Keep paragraph structure: blank-line-separated blocks stay separate
            # paragraphs instead of collapsing into one wall of text.
            out.append(body)

    md = "\n".join(out).strip()
    return md or "(no content extracted)"


@router.post("/documents/{doc_id}/markdown", response_model=ExportResponse)
def export_markdown(
    doc_id: str,
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_role("admin", "reviewer")),
) -> dict:
    """Convert the document to GitHub-flavoured Markdown.

    M43.P1.5.QF · multi-pass over 8K-char windows of the doc, generating
    markdown per window and concatenating. User reported the prior
    single-pass cap (8K chars input, 2400 max_tokens output) was
    silently truncating multi-page docs — a 2-page Google tax invoice
    came out as just the header lines, losing line items and totals.
    Now we cover the whole doc in chunks and ship the concatenated
    result, with a clear `---` separator between windows so the markdown
    structure stays readable.

    Bounds: 8 windows max (~64K chars · roughly 30 dense pages). Past
    that we emit a `[...remainder truncated...]` marker so the reviewer
    knows there's more.
    """
    tid = user.org_id
    doc = _load_doc(db, tid, doc_id)

    # M44.P4 · DB-first · serve from document_artifacts when available.
    # Zero LLM calls. The artifact was generated once at ingest with
    # proper retries and rate-limit handling.
    art = db.scalar(
        select(DocumentArtifact).where(DocumentArtifact.document_pk == doc.pk)
    )
    if art is not None and art.full_text_md:
        # M44.P11.2 · artifact was built from tokenized chunks when protected;
        # detokenize only when an authorized user revealed the doc.
        body = _reveal_fn(db, doc_id)(art.full_text_md)
        return {"docId": doc_id, "format": "markdown", "body": body}
    # If artifact exists but markdown was skipped for this tier (e.g.
    # summary_only or skipped), tell the user upfront instead of burning
    # tokens regenerating on demand.
    if art is not None and not art.full_text_md and art.processing_strategy in ("summary_only", "skipped"):
        raise HTTPException(
            status_code=409,
            detail=(
                f"Document is too large for full Markdown extraction "
                f"(strategy={art.processing_strategy} · {art.page_count} pages · "
                f"{art.char_count} chars). Use the Summary tab instead — "
                f"summary is always available."
            ),
        )

    # M51 · LAZY markdown · full/reduced docs have their markdown deferred at
    # ingest (it's the priciest artifact). Generate the multi-pass markdown NOW,
    # CACHE it on the artifact so subsequent views are instant, and return it.
    if art is not None and not art.full_text_md and art.processing_strategy in ("full", "reduced"):
        from app.jobs.materialize_artifacts import (
            _MD_MAX_WINDOWS_FULL, _MD_MAX_WINDOWS_REDUCED, _gen_markdown,
        )
        full_text = _doc_text_excerpt(db, doc.pk, max_chars=200_000)
        max_windows = _MD_MAX_WINDOWS_FULL if art.processing_strategy == "full" else _MD_MAX_WINDOWS_REDUCED
        md = ""
        try:
            md = _gen_markdown(full_text, max_windows=max_windows)
        except Exception as e:  # noqa: BLE001
            log.warning("lazy markdown generation failed for %s: %s", doc_id, e)
        if md:
            art.full_text_md = md
            db.commit()
            return {"docId": doc_id, "format": "markdown",
                    "body": _reveal_fn(db, doc_id)(md)}
        # if generation failed, fall through to the deterministic path below

    # Fallback · doc never went through P4 materialization (predates the
    # feature, or worker hadn't run yet). Do the legacy on-demand
    # generation. This is the slow path that times out on long docs;
    # for newer docs the artifact path above handles them.
    text = _doc_text_excerpt(db, doc.pk, max_chars=120_000)

    # M46 · Documents product · render Markdown deterministically from the rich
    # extraction (records table included) + text. No LLM call, so it never fails
    # on a provider rate-limit (the Gemini-only on-demand path 429s under load).
    if get_settings().product == "documents":
        # M46 · LLM-structure the raw body into clean GFM (paid Haiku, cached,
        # DETERMINISTIC render only — no blocking on-demand LLM. The on-demand LLM
        # structurer (`_structure_body_md`) proved unreliable: the free-tier model
        # rate-limits and hangs 30-60s, so the /markdown request routinely timed out
        # ("markdown keeps failing"), and for long docs it only saw the first ~12K chars
        # → a partial result LESS complete than this. The deterministic markdown (records
        # table + structured fields + full text) is instant and complete. Nicely
        # LLM-structured GFM still comes from the BACKGROUND materialization artifact
        # (generated at ingest with proper retries) when present — served above.
        body = _reveal_fn(db, doc_id)(_deterministic_markdown(doc, text))
        return {"docId": doc_id, "format": "markdown", "body": body}

    if not text.strip():
        raise HTTPException(status_code=409, detail="Document has no extractable text")

    WINDOW = 8000
    MAX_WINDOWS = 8

    # Split on paragraph/double-newline boundary when possible so we don't
    # cut mid-sentence. Fall back to hard slice when a single block is
    # bigger than the window.
    def _split_windows(s: str) -> list[str]:
        out: list[str] = []
        cursor = 0
        n = len(s)
        while cursor < n and len(out) < MAX_WINDOWS:
            end = min(cursor + WINDOW, n)
            if end < n:
                # Walk back to the nearest paragraph break
                cut = s.rfind("\n\n", cursor + WINDOW // 2, end)
                if cut > 0:
                    end = cut + 2
            out.append(s[cursor:end])
            cursor = end
        return out

    windows = _split_windows(text)
    sys_template = (
        "Convert the document segment below to clean GitHub-flavoured "
        "Markdown. Preserve headings, bullet/numbered lists, and tables. "
        "Strip page headers, footers, and pagination artefacts. Output "
        "Markdown ONLY — no preamble, no closing commentary, no code "
        "fences around the whole output. {pos_hint}"
    )
    parts: list[str] = []
    for i, win in enumerate(windows):
        if i == 0 and len(windows) == 1:
            pos_hint = ""
        elif i == 0:
            pos_hint = "This is the BEGINNING of a multi-window doc; do not summarise — transcribe everything."
        elif i == len(windows) - 1:
            pos_hint = "This is the FINAL window. Do not repeat content from earlier windows; just continue."
        else:
            pos_hint = "This is a MIDDLE window. Do not repeat content from earlier windows; just continue."
        sys = sys_template.format(pos_hint=pos_hint)
        try:
            part = _llm_one_shot(db, sys, win, max_tokens=4096).strip()
            if part:
                parts.append(part)
        except Exception as e:  # noqa: BLE001
            # Don't fail the whole export if one window misbehaves
            parts.append(f"<!-- markdown window {i+1} failed: {e} -->")

    # When the LLM produced nothing (e.g. provider rate-limit), fall back to the
    # deterministic render so the Markdown tab still returns useful content.
    md = ("\n\n---\n\n".join(parts)) if parts else _deterministic_markdown(doc, text)
    # Truncation marker if we capped at MAX_WINDOWS
    if len(_split_windows(text)) > MAX_WINDOWS or (len(text) > sum(len(w) for w in windows) + 100):
        md += "\n\n*[...remainder truncated · document longer than 8 windows]*"
    return {"docId": doc_id, "format": "markdown", "body": md}


@router.post("/documents/{doc_id}/json", response_model=ExportResponse)
def export_json(
    doc_id: str,
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_role("admin", "reviewer")),
) -> dict:
    """Three-tier JSON return: P4 artifact → extracted_fields → on-demand LLM.
    The first two are zero-LLM; the last is the legacy fallback for docs
    that pre-date both."""
    import json as _json
    tid = user.org_id
    doc = _load_doc(db, tid, doc_id)

    # M51 · the JSON tab now serves the AUTHORITATIVE extracted_fields — it's
    # role-aware, exists for EVERY processed doc (not just the "full" tier), and
    # costs zero extra tokens. (We dropped the separate structured_json
    # materialization; nothing else consumed it.) Order:
    #   1. extracted_fields  (primary · all docs · free)
    #   2. legacy structured_json  (docs materialized before this change)
    #   3. on-demand LLM  (docs with neither)
    if doc.extracted_fields and doc.extracted_fields.get("fields"):
        body = _reveal_fn(db, doc_id)(_json.dumps(doc.extracted_fields, indent=2))
        return {"docId": doc_id, "format": "json", "body": body}
    art = db.scalar(
        select(DocumentArtifact).where(DocumentArtifact.document_pk == doc.pk)
    )
    if art is not None and art.structured_json:
        body = _reveal_fn(db, doc_id)(_json.dumps(art.structured_json, indent=2))
        return {"docId": doc_id, "format": "json", "body": body}
    text = _doc_text_excerpt(db, doc.pk, max_chars=8000)
    if not text.strip():
        raise HTTPException(status_code=409, detail="Document has no extractable text")
    sys = (
        "Extract the document into structured JSON. Use sensible top-level field names "
        "based on the content (e.g. 'parties', 'effective_date', 'amount', 'terms'). "
        "Return VALID JSON only — no preamble, no Markdown code fences, no commentary. "
        "If a field is unknown leave its value as an empty string."
    )
    body = _llm_one_shot(db, sys, text, max_tokens=1500).strip()
    # Strip code fences if the model added them anyway.
    if body.startswith("```"):
        body = body.split("```", 2)[1]
        if body.startswith("json"):
            body = body[4:]
        body = body.strip("` \n")
    return {"docId": doc_id, "format": "json", "body": body}

