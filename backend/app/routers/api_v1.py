"""v1 public API for partners and SDK consumers.

Auth is per-partner API keys via app.api_clients.require_client (NOT the cookie
session).

This module hosts the STATEFUL "match a requirement against a shared folder"
endpoint: a partner key granted a customer's group finds the evidence doc(s) for
a requirement using the PRE-CLASSIFIED corpus (no reprocessing, no pushing the
corpus to the partner).
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Path, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api_clients import Caller, require_client
from app.db import get_current_tenant, get_session

router = APIRouter()
log = logging.getLogger("docaiq.api_v1")


class AuditMatchPayload(BaseModel):
    framework: str | None = None        # echoed, e.g. "KYC" / "SOC2"
    requirementId: str | None = None    # echoed, e.g. "ID-01"
    requirement: str | None = None      # free text, e.g. "National ID / Passport on file"
    docType: str | None = None          # optional type hint, e.g. "national id"


def _doc_evidence(d) -> dict:
    ef = d.extracted_fields or {}
    fields = ef.get("fields") if isinstance(ef, dict) and isinstance(ef.get("fields"), dict) else ef
    return {
        "docId": d.id_external,
        "name": d.name,
        "docType": d.doc_type,
        "confidence": (ef.get("confidence") if isinstance(ef, dict) else None) or d.doc_type_confidence,
        "fields": fields if isinstance(fields, dict) else {},
        "citations": ef.get("field_bboxes", {}) if isinstance(ef, dict) else {},
    }


@router.post("/groups/{group_id}/audit/match")
async def group_audit_match(
    payload: AuditMatchPayload,
    group_id: int = Path(...),
    caller: Caller = Depends(require_client("audit:match")),
    db: Session = Depends(get_session),
) -> dict:
    """Match an audit requirement against the documents shared into a group (the
    'shared folder'). Finds candidate docs by their CLASSIFIED type from the
    pre-indexed corpus — no reprocessing, and only the matching evidence (not the
    whole corpus) is returned. Authorized only for keys granted this group."""
    if not caller.may_access_group(group_id):
        raise HTTPException(status_code=403, detail=f"API key not granted access to group {group_id}")

    from app.services import workspace_chat as wc
    tid = get_current_tenant()
    docs = wc.resolve_scope_docs(db, tid, vendor_pk=None, group_id=group_id)

    # Resolve the document-type to look for: explicit hint, else inferred from
    # the requirement text against the product's type-synonym families.
    phrase = (payload.docType or "").strip().lower() or None
    if not phrase and payload.requirement:
        t = payload.requirement.lower()
        for p in sorted(wc._TYPE_SYNONYMS, key=len, reverse=True):
            if p in t:
                phrase = p
                break

    matched = wc._matched_docs_by_type(docs, phrase) if phrase else docs
    matches = [_doc_evidence(d) for d in matched]
    return {
        "framework": payload.framework,
        "requirementId": payload.requirementId,
        "groupId": group_id,
        "docTypePhrase": phrase,
        "satisfied": len(matches) > 0,
        "matchCount": len(matches),
        "scannedDocs": len(docs),
        "matches": matches,
    }


def _human_size(n: int) -> str:
    f = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if f < 1024 or unit == "GB":
            return f"{f:.0f} {unit}"
        f /= 1024
    return f"{f:.0f} GB"


# ── API 1 · group ingress — PUT a document into a group ──
@router.post("/groups/{group_id}/documents", status_code=201)
async def group_ingest(
    group_id: int = Path(...),
    file: UploadFile = File(...),
    external_id: str | None = Form(None),
    source: str | None = Form(None),
    caller: Caller = Depends(require_client("audit:ingest")),
    db: Session = Depends(get_session),
) -> dict:
    """Upload a document into a group (the 'shared folder'). DocAIQ stores +
    ingests it (parse/chunk/embed/classify) as usual; the partner gets back a
    docId. Idempotent on sha256 within the group. Authorized only for keys
    granted this group."""
    if not caller.may_access_group(group_id):
        raise HTTPException(status_code=403, detail=f"API key not granted access to group {group_id}")

    import io
    import secrets

    from app import storage
    from app.config import get_settings
    from app.documents_scope import set_current_owner_user_pk
    from app.orm import Document, DocumentGroup, DocumentGroupShare
    from app.queue import enqueue_ingest
    from app.repositories import documents as repo

    tid = get_current_tenant()
    grp = db.scalar(select(DocumentGroup).where(
        DocumentGroup.pk == group_id, DocumentGroup.tenant_id == tid))
    if grp is None:
        raise HTTPException(status_code=404, detail="group not found")

    try:
        raw, sha = storage.hash_and_buffer(file.file, get_settings().max_upload_bytes)
    except ValueError as e:
        raise HTTPException(status_code=413, detail=str(e))
    try:
        mime = storage.validate_upload(raw, file.content_type, file.filename)
    except ValueError as e:
        raise HTTPException(status_code=415, detail=str(e))

    # Idempotency: same bytes already in this group → return the existing docId.
    in_group = select(DocumentGroupShare.document_pk).where(DocumentGroupShare.group_id == group_id)
    existing = db.scalar(select(Document).where(
        Document.tenant_id == tid, Document.sha256 == sha, Document.pk.in_(in_group)))
    if existing is not None:
        return {"docId": existing.id_external, "groupId": group_id, "name": existing.name,
                "status": "exists", "sha256": sha, "externalId": external_id}

    # New doc — owned by the group's owner so it lands in the group's scope.
    set_current_owner_user_pk(grp.created_by_user_id)
    s3_key = f"{tid}/documents/{sha[:2]}/{sha}-{secrets.token_hex(8)}"
    storage.put_object(s3_key, io.BytesIO(raw), content_type=mime)
    row = repo.create_upload(
        db, id_external=f"doc-ing-{sha[:10]}-{secrets.token_hex(3)}",
        name=storage.sanitize_filename(file.filename),
        path=f"Partner ingress › group {group_id}",
        size=_human_size(len(raw)), pages=1, mime_type=mime, sha256=sha, s3_key=s3_key,
        uploaded_by=grp.created_by_email or caller.name, source=(source or "partner"),
    )
    row.ingestion_status = "pending"
    db.add(DocumentGroupShare(tenant_id=tid, document_pk=row.pk, group_id=group_id))
    db.commit()
    await enqueue_ingest(row.pk, tid)
    return {"docId": row.id_external, "groupId": group_id, "name": row.name,
            "status": "ingested", "sha256": sha, "externalId": external_id}


# ── API 2 · grounded QA over a group ────────────────────
# A partner may request a model, but only from this vetted set — otherwise a caller could point every
# request at the most expensive model and drain the platform's LLM budget. Unknown → server default.
_ALLOWED_BYO_MODELS = {
    "dashscope/qwen-max", "dashscope/qwen-plus", "dashscope/qwen-vl-max",
    "anthropic/claude-haiku-4.5", "google/gemini-2.5-flash",
}


def _safe_model(m: str | None) -> str | None:
    return m if (m and m in _ALLOWED_BYO_MODELS) else None


def _build_citations(answer: str, ev: list, min_support, name_by_id: dict | None = None) -> list[dict]:
    """Per-sentence source attribution → the public {docId,name,page,quote} citation
    list, shared by `group_answer` and `_rag_answer_for_owner`. Falls back to the raw
    evidence passages when the attributor finds no sentence-level support. Pass
    `name_by_id` (external-id → display name) to remap doc names (group scope); omit
    to use each hit's own `document_name` (owner scope)."""
    from app import sentence_citations as sc
    passages = [{"chunkPk": int(h.chunk_pk), "page": int(h.page), "docId": h.document_id_external,
                 "docName": (name_by_id.get(h.document_id_external, h.document_name)
                             if name_by_id is not None else h.document_name),
                 "text": h.text or "", "quote": (h.text or "")[:200]} for h in ev]
    attrs = sc.attribute(sc.split_sentences(answer), passages, min_support=min_support)
    cites = sc.citations_from_attributions(attrs) or [
        {k: v for k, v in p.items() if k != "text"} for p in passages]
    return [{"docId": c.get("docId"), "name": c.get("docName"),
             "page": c.get("page"), "quote": c.get("quote")} for c in cites]


class AnswerPayload(BaseModel):
    question: str
    topK: int = 8
    # Optional prior turns for multi-turn use ([{role:"user"|"ai", text:"..."}]).
    # Lets an assistant integrator keep conversation context (follow-ups like
    # "and the second one?") — the in-app chat already does this; this exposes it
    # on the partner API. Capped + formatted server-side.
    history: list[dict] | None = None
    # #4 · BYO-model: override the answer model (e.g. "openrouter/anthropic/claude-…",
    # "dashscope/qwen-max"). Routed via the platform's keys. None → tenant default.
    model: str | None = None


@router.post("/groups/{group_id}/answer")
async def group_answer(
    payload: AnswerPayload,
    group_id: int = Path(...),
    caller: Caller = Depends(require_client("audit:match")),
    db: Session = Depends(get_session),
) -> dict:
    """Answer a question grounded ONLY in the documents shared into a group, with
    citations + calibrated abstention (returns grounded=false / 'no evidence' when
    the group can't support an answer). Replaces a partner's local retrieval +
    llm_one_shot for security-questionnaire drafting etc."""
    if not caller.may_access_group(group_id):
        raise HTTPException(status_code=403, detail=f"API key not granted access to group {group_id}")
    question = (payload.question or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="question is required")

    from app import abstention, retrieval
    from app.config import get_settings
    from app.services import doc_chat, workspace_chat as wc

    tid = get_current_tenant()
    s = get_settings()
    docs = wc.resolve_scope_docs(db, tid, vendor_pk=None, group_id=group_id)
    name_by_id = {d.id_external: d.name for d in docs}
    doc_pks = [d.pk for d in docs]

    def _refuse():
        return {"answer": abstention.refusal_message(n_docs=len(docs)), "grounded": False,
                "citations": [], "confidence": "none"}

    if not doc_pks:
        return _refuse()
    # Multi-turn · normalize prior turns and (for follow-ups) augment the
    # retrieval query with the most recent prior user turn so "what about the
    # second one?" still retrieves the right context.
    hist = [{"role": m.get("role"), "text": (m.get("text") or "")}
            for m in (payload.history or []) if isinstance(m, dict) and m.get("text")]
    prev_user = next((m["text"] for m in reversed(hist) if m["role"] == "user"), "")
    retrieval_query = (f"{prev_user} {question}".strip() if prev_user else question)[:1000]
    hits = retrieval.retrieve(db, retrieval_query, top_k=max(1, min(payload.topK, 12)), doc_pks=doc_pks)
    abstain, _why = abstention.assess_evidence(
        [getattr(h, "score", None) for h in hits],
        min_hits=s.chat_abstain_min_hits, min_top_score=s.chat_abstain_min_top_score)
    if abstain or not hits:
        return _refuse()

    ev = hits[:6]
    from app.services.chat_pipeline import _format_history_block, format_evidence_block
    evidence_block = format_evidence_block(ev, cap=500, show_name=True)
    system = (
        "You answer STRICTLY from the EVIDENCE about the documents in this group. "
        "Cite the source document. If the evidence doesn't answer the question, say you "
        "don't have it on file. Never invent numbers, names, or dates.")
    history_block = _format_history_block(hist)   # "" when no history; capped internally
    try:
        answer = (doc_chat.llm_one_shot(
            db, system,
            f"{history_block}Evidence:\n{evidence_block}\n\nQuestion: {question}",
            max_tokens=500, model=_safe_model(payload.model),
            # mask field-derived PII (names/IDs) in the evidence — parity with in-app chat
            extra_terms=wc._pii_extra_terms(docs)) or "").strip()
    except Exception:  # noqa: BLE001
        answer = ""
    if not answer:
        return {"answer": "", "grounded": False, "citations": [], "confidence": "none"}

    grounded = True
    try:
        from app.agents import chat_guardrail
        grounded, _issue = chat_guardrail.critique(db, question, evidence_block, answer)
    except Exception:  # noqa: BLE001
        pass

    return {
        "answer": answer,
        "grounded": grounded,
        "confidence": "high" if grounded else "low",
        "citations": _build_citations(answer, ev, s.chat_sentence_support_min, name_by_id=name_by_id),
    }


def _user_ready_doc_pks(db: Session, owner_pk: int) -> list[int]:
    from app.orm import Document
    return [d.pk for d in db.query(Document).filter(
        Document.owner_user_id == owner_pk, Document.ingestion_status == "ready").all()]


class MeAnswerPayload(BaseModel):
    question: str
    topK: int = 8
    history: list[dict] | None = None


def _rag_answer_for_owner(db: Session, owner_pk: int, question: str,
                          top_k: int = 8, history: list[dict] | None = None) -> dict:
    """Shared RAG-over-one-user's-documents core: citations + calibrated abstention. Used by both
    the SSO `/me/answer` (chataiq) and the key-authed `/ask` (enterprise self-serve API)."""
    question = (question or "").strip()[:4000]  # cap input (DoS / cost guard)
    if not question:
        raise HTTPException(status_code=400, detail="question is required")
    # Deterministic handlers first — the same brain as the in-app chat (accurate counts, money,
    # identity, watchlist, entities). Falls through to grounded RAG below. Owner context is set so
    # the handlers only ever see this owner's documents.
    try:
        from app.config import get_settings as _gs
        from app.documents_scope import set_current_owner_user_pk
        from app.services import workspace_chat as _wc
        set_current_owner_user_pk(owner_pk)
        _det = _wc.deterministic_answer(db, _gs().tenant_id, question, include_overview=True)
        if _det:
            return {"answer": _det, "grounded": True, "confidence": "high", "citations": []}
    except Exception:  # noqa: BLE001 — never let a handler bug block the RAG fallback
        pass
    from app import abstention, retrieval
    from app.config import get_settings
    from app.services import doc_chat
    from app.services.chat_pipeline import _format_history_block, format_evidence_block
    s = get_settings()
    payload = MeAnswerPayload(question=question, topK=top_k, history=history)
    doc_pks = _user_ready_doc_pks(db, owner_pk)

    def _refuse():
        return {"answer": abstention.refusal_message(n_docs=len(doc_pks)), "grounded": False,
                "citations": [], "confidence": "none"}

    if not doc_pks:
        return _refuse()
    hist = [{"role": m.get("role"), "text": (m.get("text") or "")}
            for m in (payload.history or []) if isinstance(m, dict) and m.get("text")]
    prev_user = next((m["text"] for m in reversed(hist) if m["role"] == "user"), "")
    rq = (f"{prev_user} {question}".strip() if prev_user else question)[:1000]
    hits = retrieval.retrieve(db, rq, top_k=max(1, min(payload.topK, 12)), doc_pks=doc_pks)
    abstain, _ = abstention.assess_evidence(
        [getattr(h, "score", None) for h in hits],
        min_hits=s.chat_abstain_min_hits, min_top_score=s.chat_abstain_min_top_score)
    if abstain or not hits:
        return _refuse()
    ev = hits[:6]
    evidence_block = format_evidence_block(ev, cap=500, show_name=True)
    system = ("You answer STRICTLY from the EVIDENCE about THIS user's documents. Cite the "
              "source document. If the evidence doesn't answer the question, say you don't "
              "have it on file. Never invent numbers, names, or dates.")
    # Mask field-derived PII (names/IDs) from the docs actually in the evidence — parity
    # with the in-app chat. Only the ~6 evidence docs are loaded, not the whole library.
    from app.orm import Document as _Doc
    from app.services import workspace_chat as _wc
    _ev_docs = list(db.scalars(select(_Doc).where(_Doc.pk.in_({h.document_pk for h in ev}))))
    try:
        answer = (doc_chat.llm_one_shot(
            db, system, f"{_format_history_block(hist)}Evidence:\n{evidence_block}\n\nQuestion: {question}",
            max_tokens=500, extra_terms=_wc._pii_extra_terms(_ev_docs)) or "").strip()
    except Exception:  # noqa: BLE001
        answer = ""
    if not answer:
        return {"answer": "", "grounded": False, "citations": [], "confidence": "none"}
    # Honest "not found" for chataiq: if the model itself abstained, surface the
    # canonical refusal with grounded:false — don't dress an "I don't have it"
    # answer up as a grounded result with citations.
    # Only treat it as a refusal when the answer LEADS with the disclaimer (first ~90 chars) — a valid
    # answer that merely mentions "…is not in the document, but the total is 500" must not be discarded.
    _low = answer.lower()[:90]
    if any(m in _low for m in (
            "don't have", "do not have", "not on file", "not in the document",
            "no information", "cannot find", "couldn't find", "unable to find",
            "isn't in", "is not in", "insufficient evidence")):
        return _refuse()
    grounded = True
    try:
        from app.agents import chat_guardrail
        grounded, _ = chat_guardrail.critique(db, question, evidence_block, answer)
    except Exception:  # noqa: BLE001
        pass
    return {"answer": answer, "grounded": grounded, "confidence": "high" if grounded else "low",
            "citations": _build_citations(answer, ev, s.chat_sentence_support_min)}


@router.post("/ask")
async def v1_ask(payload: MeAnswerPayload,
                 caller: Caller = Depends(require_client("ask")),
                 db: Session = Depends(get_session)) -> dict:
    """Enterprise self-serve API: RAG answer over the API-KEY OWNER's own documents, with citations
    and calibrated abstention. Requires an owner-scoped key (created in the user's account); a
    tenant/partner key is rejected so cross-tenant data can never be reached through this endpoint."""
    if caller.owner_user_id is None:
        raise HTTPException(status_code=403,
                            detail="this endpoint needs an owner-scoped key (create one in your account → API keys)")
    return _rag_answer_for_owner(db, caller.owner_user_id, payload.question, payload.topK, payload.history)


@router.get("/documents")
def v1_documents(limit: int = 100,
                 caller: Caller = Depends(require_client("documents:read")),
                 db: Session = Depends(get_session)) -> dict:
    """Enterprise self-serve API: list the key owner's ready documents (id, name, type, dates)."""
    if caller.owner_user_id is None:
        raise HTTPException(status_code=403, detail="this endpoint needs an owner-scoped key")
    from app.orm import Document
    rows = db.query(Document).filter(
        Document.owner_user_id == caller.owner_user_id,
        Document.ingestion_status == "ready").order_by(Document.created_at.desc()).limit(max(1, min(limit, 500))).all()
    return {"documents": [{"id": d.id_external, "name": d.name, "type": d.doc_type,
                           "createdAt": d.created_at.isoformat() if d.created_at else None} for d in rows],
            "count": len(rows)}
