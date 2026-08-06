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
from pydantic import AliasChoices, BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import retrieval
from app.config import get_settings
from app.db import get_session, get_current_tenant, get_current_vendor_pk
from app.documents_scope import get_current_owner_user_pk
from app.orm import ChatMessage, Document, DocumentArtifact, DocumentChunk
from app.security import CurrentUser, require_role
from app.llm.prompts import get_prompt
from app.services import doc_chat as doc_chat_service
# Re-exports kept for backwards compatibility with any test or external
# code that imported the private helpers from here (TODO #25 conservative
# extraction — see services/doc_chat.py).
_FACTS_NOT_FOUND_SENTINEL = doc_chat_service.FACTS_NOT_FOUND_SENTINEL
_doc_text_excerpt = doc_chat_service.doc_text_excerpt
_llm_one_shot = doc_chat_service.llm_one_shot
_try_answer_from_facts = doc_chat_service.try_answer_from_facts
_backfill_citation_bboxes = doc_chat_service._backfill_citation_bboxes

log = logging.getLogger(__name__)
router = APIRouter()


# ── Pydantic shapes ──────────────────────────────────────────────────────

class Citation(BaseModel):
    """Citation JSONB shape. Historically two key names ended up in the
    DB — chunkPk (frontend-friendly camelCase) from services/doc_chat,
    and chunk_pk (snake_case) from document_agent and earlier agents.
    The alias accepts both so old chat messages still validate."""
    chunkPk: int = Field(..., validation_alias=AliasChoices("chunkPk", "chunk_pk"))
    page: int
    bbox: dict | None = None
    quote: str | None = None

    model_config = {"populate_by_name": True}


class Message(BaseModel):
    id: int
    role: str  # user | ai | system
    text: str
    citations: list[Citation] = []
    confidence: float | None = None
    createdAt: str | None = None
    # M44.P7 · expose the pipeline-step that produced this answer
    # ('facts_det', 'full_doc_ctx', 'agent', 'cache_hit', etc).
    # Useful for the UI badge + telemetry + debugging which path fired.
    meta: str | None = None


class ChatThread(BaseModel):
    docId: str
    summary: str | None = None
    messages: list[Message] = []


class PostMessagePayload(BaseModel):
    text: str


class ExportResponse(BaseModel):
    docId: str
    format: str   # "markdown" | "json"
    body: str


# ── Helpers ──────────────────────────────────────────────────────────────

def _msg_to_dict(m: ChatMessage) -> dict:
    return {
        "id": m.pk,
        "role": m.role,
        "text": m.text,
        "citations": m.citations or [],
        "confidence": m.confidence,
        "createdAt": m.created_at.isoformat() if m.created_at else None,
        "meta": m.meta,
        # Inline reasoning steps (from the [[THINKING]] block) → rendered as a
        # collapsible 'Thinking' disclosure above the answer. Empty for older messages.
        "thinking": (m.trace if isinstance(m.trace, list) else []),
    }


def _load_doc(db: Session, tenant_id: str, doc_id: str) -> Document:
    stmt = select(Document).where(
        Document.tenant_id == tenant_id,
        Document.id_external == doc_id,
    )
    # M17 · defense-in-depth — even though these chat endpoints are role-gated
    # to admin/reviewer today, scope a vendor-only caller to their own docs so
    # the helper stays safe if the role gate ever changes.
    vpk = get_current_vendor_pk()
    if vpk is not None:
        stmt = stmt.where(Document.vendor_pk == vpk)
    # M46 · documents product · scope to the caller's own workspace so one user
    # can't open another user's doc-chat thread / summary / PII by guessing the
    # doc id. Group-shared docs (group_id in one of the caller's groups) are
    # visible too. No-op in the auditing product (owner scope never set there).
    uid = get_current_owner_user_pk()
    if uid is not None:
        from sqlalchemy import or_
        from app.orm import DocumentGroupMember, DocumentGroupShare
        _my_groups = select(DocumentGroupMember.group_id).where(
            DocumentGroupMember.user_id == uid)
        _shared_to_me = select(DocumentGroupShare.document_pk).where(
            DocumentGroupShare.group_id.in_(_my_groups))
        stmt = stmt.where(or_(Document.owner_user_id == uid,
                              Document.pk.in_(_shared_to_me)))
    doc = db.scalar(stmt)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"Document {doc_id} not found")
    return doc


def _assert_message_visible(db: Session, msg: ChatMessage, user: CurrentUser) -> None:
    """Owner-scope guard for endpoints that load a ChatMessage by its pk.

    No-op in the auditing product (owner scope never set). In the documents
    product, ensure the message belongs to the caller — via its parent document
    (per-doc chat) or its workspace_key (cross-doc chat) — so a user can't read
    a trace of, or vote/feedback on, someone else's answer by guessing the
    sequential integer message_pk. M46 isolation hardening.
    """
    uid = get_current_owner_user_pk()
    if uid is None:
        return
    if msg.doc_id_external:
        _load_doc(db, user.org_id, msg.doc_id_external)  # raises 404 if not owned
    elif (msg.workspace_key or "") != f"user:{uid}":
        raise HTTPException(status_code=404, detail="Chat message not found")


def _summary_message(db: Session, tenant_id: str, doc_id: str) -> ChatMessage | None:
    """Find the cached AI summary (the first AI message of this thread tagged
    with meta='summary'). Returns None if not yet generated."""
    return db.scalar(
        select(ChatMessage)
        .where(
            ChatMessage.tenant_id == tenant_id,
            ChatMessage.doc_id_external == doc_id,
            ChatMessage.role == "ai",
            ChatMessage.meta == "summary",
        )
        .order_by(ChatMessage.pk)
    )


# ── Endpoints ────────────────────────────────────────────────────────────

@router.get("/documents/{doc_id}/chat", response_model=ChatThread)
def get_chat(
    doc_id: str,
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_role("admin", "reviewer")),
) -> dict:
    tid = user.org_id
    _load_doc(db, tid, doc_id)  # 404s if the doc isn't in this tenant
    msgs = db.scalars(
        select(ChatMessage)
        .where(
            ChatMessage.tenant_id == tid,
            ChatMessage.doc_id_external == doc_id,
        )
        .order_by(ChatMessage.pk)
    ).all()
    summary_msg = next((m for m in msgs if m.meta == "summary"), None)
    reveal = _reveal_fn(db, doc_id)
    out_msgs = []
    for m in msgs:
        d = _msg_to_dict(m)
        d["text"] = reveal(d["text"])
        for c in d.get("citations") or []:
            if isinstance(c, dict) and c.get("quote"):
                c["quote"] = reveal(c["quote"])
        out_msgs.append(d)
    return {
        "docId": doc_id,
        "summary": reveal(summary_msg.text) if summary_msg else None,
        "messages": out_msgs,
    }


def _reveal_fn(db: Session, doc_id: str):
    """Return a text-mapping function. When the document is PII-protected AND
    an authorized user has revealed it, detokenize placeholders back to real
    values; otherwise return text unchanged (placeholders stay)."""
    tid = get_current_tenant()
    doc = db.scalar(select(Document).where(
        Document.tenant_id == tid, Document.id_external == doc_id,
    ))
    if doc is None or not (doc.pii_protected and doc.pii_revealed):
        return lambda t: t
    from app import pii_vault
    mapping = pii_vault.load_mapping(db, doc.pk)
    if not mapping:
        return lambda t: t

    def _apply(t: str) -> str:
        if not t:
            return t
        for token, value in mapping.items():
            t = t.replace(token, value)
        return t
    return _apply


@router.post("/documents/{doc_id}/pii/reveal")
def reveal_pii(
    doc_id: str,
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_role("admin", "reviewer")),
) -> dict:
    """Reveal (detokenize) a PII-protected document's real values for
    authorized internal viewing. Owner/admin/reviewer only; audited."""
    tid = user.org_id
    doc = _load_doc(db, tid, doc_id)
    if not doc.pii_protected:
        return {"docId": doc_id, "piiProtected": False, "piiRevealed": False}
    doc.pii_revealed = True
    db.commit()
    log.info("PII REVEAL · doc=%s tenant=%s by=%s", doc_id, tid, user.email)
    return {"docId": doc_id, "piiProtected": True, "piiRevealed": True}


@router.post("/documents/{doc_id}/pii/hide")
def hide_pii(
    doc_id: str,
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_role("admin", "reviewer")),
) -> dict:
    """Re-hide a previously-revealed document (back to placeholders)."""
    tid = user.org_id
    doc = _load_doc(db, tid, doc_id)
    doc.pii_revealed = False
    db.commit()
    log.info("PII HIDE · doc=%s tenant=%s by=%s", doc_id, tid, user.email)
    return {"docId": doc_id, "piiProtected": doc.pii_protected, "piiRevealed": False}


@router.post("/documents/{doc_id}/summary", response_model=Message)
def generate_summary(
    doc_id: str,
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_role("admin", "reviewer")),
) -> dict:
    """Generate (or regenerate) the on-open document summary. Cached as a
    chat_messages row with meta='summary' — subsequent GETs read the
    cached one, no extra LLM call."""
    tid = user.org_id
    doc = _load_doc(db, tid, doc_id)
    if doc.ingestion_status != "ready":
        raise HTTPException(status_code=409, detail=f"Document not ready (status={doc.ingestion_status})")

    # M44.P5.1 · prefer the materialized summary from document_artifacts.
    # Zero LLM calls. The artifact was generated once at ingest by the
    # worker with proper retries — much more reliable than re-generating
    # on demand against a rate-limited free-tier model.
    art = db.scalar(
        select(DocumentArtifact).where(DocumentArtifact.document_pk == doc.pk)
    )
    if art is not None and art.summary_long:
        # Replace any prior cached summary row so the UI gets just one.
        prior = _summary_message(db, tid, doc_id)
        if prior is not None:
            db.delete(prior)
            db.flush()
        msg = ChatMessage(
            tenant_id=tid,
            requirement_id_external=None,
            doc_id_external=doc_id,
            role="ai",
            text=art.summary_long,
            meta="summary",
        )
        db.add(msg)
        db.commit()
        return _msg_to_dict(msg)

    # Fallback · doc pre-dates P4 materialization OR worker hasn't run
    # yet. Do the legacy on-demand LLM generation. This is the slow path
    # that 429s today on free-tier models; for newer docs the artifact
    # path above handles them in zero LLM calls.
    text = _doc_text_excerpt(db, doc.pk, max_chars=6000)
    if not text.strip():
        raise HTTPException(status_code=409, detail="Document has no extractable text")

    sys = get_prompt("doc_chat_summary")
    # M43.P1.5.QF · bumped 400→800. The 5-section template (Type / Parties /
    # Period / Key claims with 4 bullets / Flags) was consistently truncating
    # at "Parties:" — user reported AI Summary card showed only 2 of 5 lines.
    summary_text = (_llm_one_shot(db, sys, text, max_tokens=800) or "").strip()
    # Defensive · if response is so short it's clearly truncated, retry once
    # with a more demanding nudge. Only when we got SOMETHING — an empty
    # response means a provider failure (e.g. 429), so retrying just burns
    # another rate-limited call; fall through to the deterministic path below.
    if 0 < len(summary_text) < 200:
        retry = _llm_one_shot(
            db,
            get_prompt("doc_chat_summary_retry"),
            text, max_tokens=800,
        )
        if retry and len(retry.strip()) > len(summary_text):
            summary_text = retry.strip()

    # M46 · Documents product · when the LLM produced nothing (rate-limit /
    # provider failure), build the summary deterministically from the
    # extraction so the Summary card is never empty.
    if not summary_text and get_settings().product == "documents":
        summary_text = _deterministic_summary(doc)
    summary_text = summary_text or "(no summary returned)"

    # Delete any prior summary row first — keeps just one cached.
    prior = _summary_message(db, tid, doc_id)
    if prior is not None:
        db.delete(prior)
        db.flush()

    msg = ChatMessage(
        tenant_id=tid,
        requirement_id_external=None,
        doc_id_external=doc_id,
        role="ai",
        text=summary_text,
        meta="summary",
    )
    db.add(msg)
    db.commit()
    return _msg_to_dict(msg)


@router.post("/documents/{doc_id}/chat/messages", response_model=Message)
def post_message(
    doc_id: str,
    payload: PostMessagePayload,
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_role("admin", "reviewer")),
) -> dict:
    # M44.P9.12 · per-user rate limit · 30 chat messages per 60s.
    # Catches accidental rapid clicks AND automated abuse.
    from app.rate_limit import rate_limit as _rate_limit
    _rate_limit(user.email, action="chat_msg")

    # M47 · free-plan monthly AI-message cap (documents product).
    if get_settings().product == "documents":
        from app.documents_scope import get_current_owner_user_pk
        from app.services import subscriptions as subs
        _uid = get_current_owner_user_pk()
        if _uid is not None:
            subs.enforce_chat(db, tenant_id=user.org_id, owner_user_id=_uid)

    """Reviewer asks a question about this document. Two-stage path:
      1. Facts-first — if the doc has structured `extracted_fields` from the
         fact_extractor (layer 1), try to answer directly from those facts.
         Returns instantly when the question is one of the deterministic
         kinds (parties, dates, totals, signatures, etc).
      2. Retrieval fallback — when the facts don't cover the question,
         retrieve chunks scoped to this doc + always include the intro
         chunks, then call the LLM with a clean Q&A prompt.
    Both paths persist the user + AI messages with citations attached."""
    tid = user.org_id
    doc = _load_doc(db, tid, doc_id)
    if doc.ingestion_status != "ready":
        raise HTTPException(status_code=409, detail=f"Document not ready (status={doc.ingestion_status})")

    text = (payload.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Empty message")

    # 1. Persist the user msg
    user_msg = ChatMessage(
        tenant_id=tid,
        requirement_id_external=None,
        doc_id_external=doc_id,
        role="user",
        text=text,
    )
    db.add(user_msg)
    db.flush()

    # Guardrail · deterministic prompt-injection / jailbreak screen (parity with
    # workspace chat). Refuse immediately — never reaches the pipeline or any LLM.
    if get_settings().documents_chat_guardrail:
        from app.agents.chat_guardrail import guard_input
        _refusal = guard_input(text)
        if _refusal:
            guard_msg = ChatMessage(
                tenant_id=tid, doc_id_external=doc_id, role="ai",
                text=_refusal, citations=[], meta="guard_input",
            )
            db.add(guard_msg)
            db.commit()
            return _msg_to_dict(guard_msg)

    # M44.P5 · Cost-ordered chat pipeline. Each step (cache_hit ·
    # identity_guard · facts_det · full_doc_ctx · agent) lives in
    # app/services/chat_pipeline.py with a declared CostClass. The
    # PIPELINE list is the ordering · a boot-time assert guarantees
    # all zero-LLM steps run before any LLM-spending step. Adding a
    # step means appending to that list at the right slot, not editing
    # this function body.
    #
    # M44.P9.2 · pull last N messages from this thread into ctx.history
    # so follow-up questions like 'and the second one?' resolve. We
    # exclude the just-persisted current user message (already in
    # ctx.text) and the AI 'summary' meta (which is on-open boilerplate
    # not a real exchange).
    from app.services.chat_pipeline import ChatContext, execute_pipeline
    _HISTORY_MAX = 8
    prior = db.scalars(
        select(ChatMessage)
        .where(
            ChatMessage.tenant_id == tid,
            ChatMessage.doc_id_external == doc_id,
            ChatMessage.pk != user_msg.pk,
            (ChatMessage.meta != "summary") | ChatMessage.meta.is_(None),
        )
        .order_by(ChatMessage.pk.desc())
        .limit(_HISTORY_MAX)
    ).all()
    history = [
        {"role": m.role, "text": m.text}
        for m in reversed(prior)
    ]
    pipeline_ctx = ChatContext(
        db=db, doc=doc, text=text, tenant_id=tid,
        doc_id_external=doc_id, user_msg_pk=user_msg.pk,
        history=history,
    )
    pipeline_result = execute_pipeline(pipeline_ctx)
    if pipeline_result is not None:
        db.commit()
        return _msg_to_dict(pipeline_result)

    # 2a. Facts-first fast path (1 LLM call · interprets the JSON blob).
    #     Returns (answer, citations) or (None, []) when the facts don't
    #     cover the question.
    facts_answer, facts_citations = _try_answer_from_facts(db, doc, text)
    if facts_answer:
        _backfill_citation_bboxes(db, doc.pk, facts_citations)
        ai_msg = ChatMessage(
            tenant_id=tid,
            requirement_id_external=None,
            doc_id_external=doc_id,
            role="ai",
            text=facts_answer,
            confidence=None,
            citations=facts_citations,
            meta="facts",
        )
        db.add(ai_msg)
        db.commit()
        return _msg_to_dict(ai_msg)

    # 2b. Retrieval fallback. Intro chunks 0-2 are always included (parties
    # / scope live on page 1 of every agreement, policy, certificate), then
    # we layer the top BM25+cosine RRF hits scoped to this doc, plus the
    # extractor's chunk_refs (intro + signature + tail) so the signature
    # page is always available to the LLM and to citations — even when the
    # tier-1 LLM 429s, retrieval still cites the right pages.
    intro_chunks = db.scalars(
        select(DocumentChunk)
        .where(DocumentChunk.document_pk == doc.pk)
        .order_by(DocumentChunk.chunk_index)
        .limit(3)
    ).all()
    forced_pks = {c.pk for c in intro_chunks}

    # Pull the extractor's chunk_refs (signature pages + tail chunks). These
    # were carefully selected to include the attestation page that BM25
    # rarely surfaces for "is signed?" questions.
    extractor_chunks: list[DocumentChunk] = []
    ef = doc.extracted_fields or {}
    extractor_refs = (ef.get("chunk_refs") if isinstance(ef, dict) else None) or []
    for ref in extractor_refs:
        pk = ref.get("chunk_pk")
        if pk and pk not in forced_pks:
            ch = db.scalar(select(DocumentChunk).where(DocumentChunk.pk == pk))
            if ch is not None:
                extractor_chunks.append(ch)

    hits = retrieval.retrieve(db, text, top_k=8, doc_id_external=doc_id)
    hit_chunks_by_pk: dict[int, DocumentChunk] = {}
    for h in hits:
        if h.chunk_pk in forced_pks or h.chunk_pk in hit_chunks_by_pk:
            continue
        chunk = db.scalar(select(DocumentChunk).where(DocumentChunk.pk == h.chunk_pk))
        if chunk is not None:
            hit_chunks_by_pk[chunk.pk] = chunk

    # Order: intro → extractor's signature/tail chunks → RRF hits. Dedupe.
    evidence_chunks: list[DocumentChunk] = list(intro_chunks)
    seen = {c.pk for c in evidence_chunks}
    for c in extractor_chunks + list(hit_chunks_by_pk.values()):
        if c.pk not in seen:
            evidence_chunks.append(c)
            seen.add(c.pk)

    # 3. Build a clean Q&A prompt — no compliance-judging frame.
    from app.services.chat_pipeline import format_evidence_block
    evidence_block = format_evidence_block(
        evidence_chunks[:10], cap=600, empty="(no chunks retrieved)")

    # The trailing clause is a clinical/reference-range guardrail: on scanned lab / medical /
    # test reports the OCR can attach a printed reference range to the WRONG adjacent test
    # (an off-by-one row shift), so the model must not turn a possibly-misaligned range into a
    # confident normal/abnormal verdict.
    system = get_prompt("doc_chat_rag")
    # Context-engineering: give the model a typed DOC-CONTEXT block (type + a
    # one-line summary) so ambiguous wording is scoped to what the document is,
    # not just the retrieved fragments. Additive — the excerpts still drive the
    # answer; this only orients the model (e.g. "balance" on a bank statement vs
    # a lab report). Kept to one line to preserve the cacheable prompt prefix.
    _dtype = getattr(doc, "doc_type", None) or (ef.get("doc_type") if isinstance(ef, dict) else None)
    _dsum = str((ef.get("notes") or ef.get("summary") or "") if isinstance(ef, dict) else "")[:220].strip()
    _doc_ctx = ""
    if _dtype or _dsum:
        _doc_ctx = f"Document type: {(_dtype or 'unknown')}" + (f" — {_dsum}" if _dsum else "") + "\n"
    user_block = (
        f"Document name: {doc.name}\n"
        f"{_doc_ctx}"
        f"Question: {text}\n\n"
        f"Evidence excerpts (E1 is the document's opening page; later E# are "
        f"the highest-scored retrieval matches):\n\n{evidence_block}"
    )

    # 4. Direct LLM call via tier-1 model — bypasses the compliance-judge
    #    cascade entirely, with falls-back to the cascade only on failure.
    # M43.P1.5.D · prepend reflexion-memory few-shot before the draft call.
    # Top similar past critiques act as "common mistakes to avoid" hints.
    reflexion_hint = _build_reflexion_few_shot(db, text)
    user_block_with_hint = (
        f"{reflexion_hint}\n\n{user_block}" if reflexion_hint else user_block
    )
    try:
        draft = _llm_one_shot(db, system, user_block_with_hint, max_tokens=600).strip()
    except Exception as e:  # noqa: BLE001
        log.exception("doc_chat: LLM call failed for doc=%s · %s", doc_id, e)
        raise HTTPException(status_code=502, detail=f"AI call failed: {e}") from e
    if not draft:
        draft = "(no response from model — try again)"

    # M43.P1.5.C · Critique-Refine loop.
    #   1. Critic reviews draft against the question + evidence chunks
    #   2. If FAIL, refine: re-prompt the validator with the critic's
    #      suggestion + corrected_hint baked into the user message
    #   3. Max 2 iterations to bound latency / cost
    #   4. Persist (question, initial_draft, critique, final) to
    #      reflexion_pairs so future similar queries inherit the hint
    from app.agents.critic import critique as _critique_fn
    initial_draft = draft
    iterations = 1
    critique_trail: str | None = None
    passed_on_first = True
    answer = draft  # start with the draft; loop may replace it

    excerpt_texts = [(c.text or "")[:1500] for c in evidence_chunks[:6]]
    doc_summary_text = None
    if isinstance(ef, dict):
        doc_summary_text = (ef.get("doc_type") or "") + " · " + str(ef.get("notes") or "")[:200]
    doc_type_classifier = getattr(doc, "doc_type", None)

    for refine_pass in range(2):
        crit = _critique_fn(
            question=text,
            draft=answer,
            source_excerpts=excerpt_texts,
            doc_summary=doc_summary_text,
            doc_type=doc_type_classifier,
        )
        if crit.passes:
            break
        # Critic flagged the draft. Build a refine prompt that exposes the
        # critique to the validator so the next pass corrects it.
        passed_on_first = False
        iterations += 1
        critique_trail = (critique_trail or "") + (
            f"\nIter {refine_pass+1}: {crit.reason}"
            + (f" · suggested: {crit.suggestion}" if crit.suggestion else "")
            + (f" · hint: {crit.corrected_hint}" if crit.corrected_hint else "")
        )
        refine_user = (
            f"{user_block}\n\n"
            "REVIEWER CRITIQUE OF YOUR PREVIOUS DRAFT (you must address this):\n"
            f"Previous draft: {answer}\n"
            f"Critique reason: {crit.reason}\n"
            + (f"Critique suggestion: {crit.suggestion}\n" if crit.suggestion else "")
            + (f"Likely correct answer per critic: {crit.corrected_hint}\n" if crit.corrected_hint else "")
            + "\nProduce a corrected answer that addresses the critique."
        )
        try:
            answer = _llm_one_shot(db, system, refine_user, max_tokens=600).strip()
            if not answer:
                answer = initial_draft  # refine produced nothing · keep first draft
                break
        except Exception as e:  # noqa: BLE001
            log.warning("doc_chat refine: pass %d failed: %s · keeping draft", refine_pass+1, e)
            answer = initial_draft
            break

    # M43.P1.5.A · persist the reflexion pair so future similar questions
    # can inherit the lesson learned. Embed the question lazily (fail-open
    # if the embed backend is unavailable).
    try:
        from app.embeddings import embed as _embed_fn
        from app.orm import ReflexionPair
        from app.documents_scope import get_current_owner_user_pk
        [q_vec] = _embed_fn([text])
        db.add(ReflexionPair(
            tenant_id=tid,
            question=text,
            question_embed=q_vec,
            draft_answer=initial_draft,
            critique=critique_trail,
            final_answer=answer,
            doc_id_external=doc_id,
            owner_user_id=get_current_owner_user_pk(),
            iterations=iterations,
            passed_on_first=passed_on_first,
        ))
        db.flush()
    except Exception as e:  # noqa: BLE001
        log.warning("doc_chat: reflexion persist failed (non-fatal): %s", e)

    # 5. Build citations. When the doc has structured field_bboxes, prepend
    #    the question-relevant ones (e.g. signature_blocks[0] for a "signed"
    #    question lands on page 3, not page 1 of the intro). Then backfill
    #    from evidence_chunks. This way citations point to fact pages even
    #    when the retrieval LLM hallucinates or gets rate-limited.
    citations: list[dict] = []
    pages_in_citations: set[int] = set()
    fbb = (ef.get("field_bboxes") if isinstance(ef, dict) else None) or {}
    if fbb:
        ans_lower = (answer or "").lower()
        q_lower = text.lower()
        # When the LLM returned empty (rate limit, etc), fall back to
        # question-text relevance so citations still land on the right page.
        no_answer = not ans_lower or ans_lower.startswith("(no response")
        haystack = (ans_lower + " " + q_lower) if no_answer else ans_lower

        def _retr_relevance(field_name: str) -> int:
            score = 0
            # Match individual word tokens of the field name so
            # "signature_blocks" hits a question like "is this signed?".
            base = field_name.lower().split("[")[0]
            tokens = [t for t in base.split("_") if len(t) >= 4]
            for tok in tokens:
                # 4-char prefix match catches sign/signed/signature, expire/expiry, etc.
                prefix = tok[:4]
                if prefix in haystack:
                    score += 2
            fields_dict = ef.get("fields") or {}
            if "[" in field_name:
                arr_name, idx_part = field_name.split("[", 1)
                try:
                    idx = int(idx_part.rstrip("]"))
                    item = (fields_dict.get(arr_name) or [])[idx]
                    if isinstance(item, dict):
                        for sv in item.values():
                            if isinstance(sv, str) and len(sv) >= 4 and sv.lower() in haystack:
                                score += 4
                                break
                except (ValueError, IndexError):
                    pass
            else:
                v = fields_dict.get(field_name)
                if isinstance(v, str) and len(v) >= 4 and v.lower() in haystack:
                    score += 4
            return score

        # Only inject when we have actual evidence the answer references facts,
        # so a generic retrieval answer doesn't get misleading fact citations.
        ranked = sorted(fbb.items(), key=lambda kv: -_retr_relevance(kv[0]))
        for fname, bb in ranked[:3]:
            if _retr_relevance(fname) == 0:
                break
            fields_dict = ef.get("fields") or {}
            if "[" in fname:
                arr_name, idx_part = fname.split("[", 1)
                try:
                    idx = int(idx_part.rstrip("]"))
                    item = (fields_dict.get(arr_name) or [])[idx]
                except (ValueError, IndexError):
                    item = None
                quote = f"{fname}: {item}" if item else fname
            else:
                quote = f"{fname}: {fields_dict.get(fname)}"
            entry: dict = {
                "chunkPk": bb.get("chunk_pk") or 0,
                "page": bb.get("page", 1),
                "bbox": None,
                "quote": str(quote)[:180],
                "fieldName": fname,
            }
            if "x0" in bb:
                entry["bbox"] = {
                    "page": bb["page"],
                    "x0": bb["x0"], "y0": bb["y0"],
                    "x1": bb["x1"], "y1": bb["y1"],
                }
                # M47 · propagate page dimensions for precise PDF overlay
                if bb.get("page_w") and bb.get("page_h"):
                    entry["bbox"]["page_w"] = bb["page_w"]
                    entry["bbox"]["page_h"] = bb["page_h"]
            citations.append(entry)
            pages_in_citations.add(bb.get("page", 1))

    # Backfill with chunk citations, preferring pages not yet covered.
    chunks_ordered = sorted(evidence_chunks[:10], key=lambda c: 0 if c.page not in pages_in_citations else 1)
    for c in chunks_ordered:
        if len(citations) >= 5:
            break
        quote = " ".join((c.text or "").split())[:200]
        citations.append({
            "chunkPk": c.pk,
            "page": c.page,
            "bbox": c.bbox,
            "quote": quote,
        })

    # 6. Persist AI reply · stamp meta with critique status so the FE
    #    can show the "challenged by critic" badge for refined answers.
    meta_marker = "critiqued" if not passed_on_first else None
    ai_msg = ChatMessage(
        tenant_id=tid,
        requirement_id_external=None,
        doc_id_external=doc_id,
        role="ai",
        text=answer,
        confidence=None,
        citations=citations,
        meta=meta_marker,
    )
    db.add(ai_msg)
    db.commit()
    return _msg_to_dict(ai_msg)


# Moved to services/doc_chat.py to break a router→agent→router import cycle.
# Re-exported here for backward compatibility.
from app.services.doc_chat import build_reflexion_few_shot as _build_reflexion_few_shot  # noqa: F401,E402


# ── Export endpoints ─────────────────────────────────────────────────────

# ── M43.P1.5.E · Reviewer thumbs feedback on chat answers ───────────────
# The reviewer can mark an AI chat answer 👍 (the answer + its critique
# trail was useful) or 👎 (the critique was wrong / unhelpful). The
# vote increments the matching counter on the reflexion_pairs row that
# was created from this chat. Few-shot retrieval prefers helpful
# critiques + filters out net-negative ones over time.

@router.post("/chat-messages/{message_pk}/helpful")
def mark_helpful(
    message_pk: int,
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_role("admin", "reviewer")),
) -> dict:
    return _vote_on_chat_message(db, message_pk, user, +1)


@router.post("/chat-messages/{message_pk}/unhelpful")
def mark_unhelpful(
    message_pk: int,
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_role("admin", "reviewer")),
) -> dict:
    return _vote_on_chat_message(db, message_pk, user, -1)


def _vote_on_chat_message(db: Session, message_pk: int, user: CurrentUser, direction: int) -> dict:
    """Find the reflexion_pairs row created closest to this chat message
    (matches on doc_id_external + final_answer text) and increment the
    helpful or unhelpful counter."""
    from app.orm import ReflexionPair as _RP
    from sqlalchemy import select as _select, desc
    msg = db.scalar(select(ChatMessage).where(
        ChatMessage.pk == message_pk,
        ChatMessage.tenant_id == user.org_id,
    ))
    if msg is None:
        raise HTTPException(status_code=404, detail="Chat message not found")
    _assert_message_visible(db, msg, user)
    # Find the matching reflexion row · most recent for this doc with
    # final_answer == this message text. (Could add a direct FK in a
    # follow-up; for now match on content + recency.)
    row = db.scalar(
        _select(_RP).where(
            _RP.tenant_id == user.org_id,
            _RP.doc_id_external == msg.doc_id_external,
            _RP.final_answer == msg.text,
        ).order_by(desc(_RP.created_at))
    )
    if row is None:
        # No reflexion row for this message — likely a pre-M43.P1.5 message
        # or one that didn't go through the critic. Silently no-op.
        return {"recorded": False, "reason": "no reflexion record for this answer"}
    if direction > 0:
        row.helpful_count += 1
    else:
        row.marked_unhelpful_count += 1
    db.commit()
    return {
        "recorded": True,
        "helpful": row.helpful_count,
        "unhelpful": row.marked_unhelpful_count,
    }


# ── M44.P2.5 · Cache stats · "X% answers served with 0 LLM calls" ──────
# Exposes a tenant-scoped breakdown of how chat answers were produced
# over the last 7 days. The Dashboard renders this as a savings chip.

@router.get("/cache-stats")
def cache_stats(
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_role("admin", "reviewer")),
) -> dict:
    """Return chat-answer path distribution + zero-LLM percentage."""
    from app import reflexion_cache
    return reflexion_cache.stats_last_7d(db, user.org_id)


# ── M44.P4 · Document artifacts · persistent doc memory ────────────────
# Serve materialized artifacts (markdown / JSON / summary / entities /
# TOC) from the document_artifacts table. ZERO LLM calls per request —
# the worker generated these once at ingest.

@router.get("/documents/{doc_id}/artifact")
def get_artifact_meta(
    doc_id: str,
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_role("admin", "reviewer")),
) -> dict:
    """Return the artifact metadata + per-kind availability. The
    frontend hydrates this once and decides which tabs to enable."""
    from app.orm import DocumentArtifact
    tid = user.org_id
    doc = _load_doc(db, tid, doc_id)
    art = db.scalar(
        select(DocumentArtifact).where(DocumentArtifact.document_pk == doc.pk)
    )
    if art is None:
        return {
            "status": "not_materialized",
            "strategy": None,
            "available": [],
            "page_count": doc.pages,
            "note": "Artifacts will be available once ingestion completes.",
        }
    return {
        "status": "ready",
        "strategy": art.processing_strategy,
        "processingNotes": art.processing_notes,
        "available": [
            kind for kind, present in [
                # M51 · markdown is lazy — available (generatable on demand) for
                # full/reduced tiers even before it's been materialized.
                ("markdown", art.full_text_md is not None
                             or art.processing_strategy in ("full", "reduced")),
                # M51 · JSON tab now serves extracted_fields (works for every
                # doc), so it's available whenever fields exist.
                ("json", bool(doc.extracted_fields and doc.extracted_fields.get("fields"))
                         or art.structured_json is not None),
                ("summaryLong", art.summary_long is not None),
                ("summaryShort", art.summary_short is not None),
                ("entities", bool(art.key_entities)),
                ("toc", bool(art.table_of_contents)),
            ] if present
        ],
        "pageCount": art.page_count,
        "charCount": art.char_count,
        "tokenCount": art.token_count,
        "createdAt": art.created_at.isoformat() if art.created_at else None,
    }


@router.get("/documents/{doc_id}/artifact/{kind}")
def get_artifact_payload(
    doc_id: str,
    kind: str,
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_role("admin", "reviewer")),
) -> dict:
    """Return the actual artifact content. kind ∈ markdown / json /
    summary-long / summary-short / entities / toc.

    404 when the artifact wasn't generated for this doc's strategy
    (e.g. asking for markdown on a 'summary_only' tier doc)."""
    from app.orm import DocumentArtifact
    tid = user.org_id
    doc = _load_doc(db, tid, doc_id)
    art = db.scalar(
        select(DocumentArtifact).where(DocumentArtifact.document_pk == doc.pk)
    )
    if art is None:
        raise HTTPException(status_code=404, detail="No artifact generated yet")

    payload_map = {
        "markdown":      art.full_text_md,
        "json":          art.structured_json,
        "summary-long":  art.summary_long,
        "summary-short": art.summary_short,
        "entities":      art.key_entities,
        "toc":           art.table_of_contents,
    }
    if kind not in payload_map:
        raise HTTPException(status_code=400, detail=f"Unknown artifact kind: {kind}")
    value = payload_map[kind]
    if value is None:
        raise HTTPException(
            status_code=404,
            detail=f"Artifact '{kind}' not generated for this document "
                   f"(strategy={art.processing_strategy})",
        )
    return {
        "kind": kind,
        "strategy": art.processing_strategy,
        "value": value,
    }


@router.post("/documents/{doc_id}/artifact/regenerate")
def regenerate_artifact(
    doc_id: str,
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_role("admin", "reviewer")),
) -> dict:
    """Manually trigger re-materialization. Useful after a doc was
    edited / re-extracted / failed mid-flight earlier. Runs inline
    (synchronous) — for big docs it can take 30-60s."""
    from app.jobs.materialize_artifacts import materialize_for_document
    tid = user.org_id
    doc = _load_doc(db, tid, doc_id)
    result = materialize_for_document(db, doc.pk, tid)
    return {"regenerated": True, "result": result}


# ── M44.P2 · Agent trace · "Show reasoning" hydration ───────────────────
# When a chat message was produced by the Document Agent (meta='agent'),
# clicking "Show reasoning" in the UI fetches the list of agent_traces
# rows that recorded each step of the ReAct loop.

@router.get("/chat-messages/{message_pk}/trace")
def get_trace(
    message_pk: int,
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_role("admin", "reviewer")),
) -> dict:
    """Return the agent's per-step trace for an AI message. Tenant-scoped."""
    from app.orm import AgentTrace
    msg = db.scalar(select(ChatMessage).where(
        ChatMessage.pk == message_pk,
        ChatMessage.tenant_id == user.org_id,
    ))
    if msg is None:
        raise HTTPException(status_code=404, detail="Chat message not found")
    _assert_message_visible(db, msg, user)
    rows = db.scalars(
        select(AgentTrace)
        .where(
            AgentTrace.tenant_id == user.org_id,
            AgentTrace.chat_message_pk == message_pk,
        )
        .order_by(AgentTrace.step_index)
    ).all()
    return {
        "chatMessagePk": message_pk,
        "steps": [
            {
                "stepIndex": r.step_index,
                "thought": r.thought,
                "actionName": r.action_name,
                "actionArgs": r.action_args,
                "observation": r.observation,
                "observationMeta": r.observation_meta,
                "error": r.error,
                "latencyMs": r.latency_ms,
            }
            for r in rows
        ],
    }


def _deterministic_summary(doc: Document) -> str:
    """M46 · Build the reviewer summary from the structured extraction — NO LLM.
    Used (documents product) when the on-demand LLM summary comes back empty
    (e.g. provider rate-limit), so the Summary card always has content."""
    ef = doc.extracted_fields or {}
    f = ef.get("fields") if isinstance(ef.get("fields"), dict) else ef

    dt = (f.get("detected_doc_type") or doc.doc_type or "document").replace("_", " ")
    parties = ", ".join(
        p.get("name") for p in (f.get("parties") or [])
        if isinstance(p, dict) and p.get("name")
    ) or (f.get("issuer") or f.get("subject_or_recipient") or "n/a")

    dates = [d for d in (f.get("dates") or []) if isinstance(d, dict)]
    start = next((d.get("value") for d in dates if "start" in (d.get("label") or "")), None)
    end = next((d.get("value") for d in dates if "end" in (d.get("label") or "")), None)
    period = f"{start or '?'} to {end or '?'}" if (start or end) else (f.get("primary_date") or "n/a")

    claims: list[str] = []
    if f.get("primary_amount"):
        claims.append(f"Primary amount: {f['primary_amount']}")
    for kf in (f.get("key_facts") or [])[:3]:
        if isinstance(kf, dict) and kf.get("value"):
            claims.append(f"{(kf.get('label') or '').replace('_', ' ')}: {kf['value']}")
    recs = f.get("records") or []
    if recs:
        claims.append(f"{len(recs)} records / line items extracted")
    claims = claims[:4] or ["See the extracted fields for details."]

    flags = (ef.get("_notes") or "").strip() or "none noted"
    lines = [
        f"Type: {dt}",
        f"Parties: {parties}",
        f"Period / scope: {period}",
        "Key claims:",
        *[f"  - {c}" for c in claims],
        f"Flags: {flags}",
    ]
    return "\n".join(lines)
