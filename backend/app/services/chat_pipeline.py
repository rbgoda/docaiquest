"""M44.P5 · Chat answer pipeline · structural enforcement of cost ordering.

Why this exists
---------------
Twice in M44 we shipped a regression because steps were added to
`post_message` in the wrong cost-order:

  · M44.P3.A · The deterministic facts path landed AFTER the agent
    path · the agent (multi-LLM) ate questions the facts path could
    have answered for free.
  · M44.P4.F · The full-doc-context path landed AFTER the agent path
    · same shape · single-LLM call buried below multi-LLM step.

Each time the fix was "move it up". The pattern was the same: a
prose-ordered function body where an editor adds new logic at a
locally-convenient location, not the globally-correct one.

This module makes the ordering data, not code. Each step is a small
function with a declared CostClass. The PIPELINE list is the ordering.
A boot-time assert enforces:

    ALL ZERO_LLM_DB_HIT steps come before ANY non-ZERO_LLM_DB_HIT step.

Adding a new step now means picking a slot in the PIPELINE list. The
assert at boot catches misordering before the first request lands.
Tests can be added to lock specific orderings in place; for now the
invariant + named slots are enough.

Steps themselves are small · each returns ChatMessage on hit or None
on miss. The executor walks the list in order until the first non-None.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.feature_flags import is_enabled, get_int
from app.orm import ChatMessage, Document, DocumentArtifact

log = logging.getLogger("docaiq.chat_pipeline")

# ── Record-listing detection (statements / invoices with line items) ───────
# Docs whose extraction carries a records array need record questions answered
# from ALL rows, not a single scalar field (facts_det) or a 3-sentence summary.
_RECORD_KEYS = ("records", "transactions", "line_items", "top_transactions")
# Explicit list intent — always defer facts_det + force a table in full-doc.
_RECORD_LISTING_RE = re.compile(
    r"\b(table|list|every|each|itemis|itemiz|transactions?|line[ -]?items?|rows?|"
    r"charges?|purchases?|expenses?|spend(?:ing|t)?|activity|entries)\b", re.I)
# Broader overview/intent — defer facts_det (so full-doc surfaces records) but
# don't force a giant table (e.g. "what's on this statement?").
_RECORD_OVERVIEW_RE = re.compile(
    r"(what'?s on|what is on|what'?s in|what is in|what'?s this|what is this|"
    r"tell me about|overview|summar|statement)", re.I)


def _doc_has_records(doc: Document) -> bool:
    ef = doc.extracted_fields if isinstance(doc.extracted_fields, dict) else {}
    f = ef.get("fields") if isinstance(ef, dict) else None
    return isinstance(f, dict) and any(
        isinstance(f.get(k), list) and f.get(k) for k in _RECORD_KEYS)


def _wants_record_listing(doc: Document, text: str) -> bool:
    """Doc has a records array AND the question explicitly wants the rows."""
    return _doc_has_records(doc) and bool(_RECORD_LISTING_RE.search(text or ""))


# ── Cost classes ──────────────────────────────────────────────────────────
class CostClass(IntEnum):
    """How expensive a step is. The pipeline invariant is:

        Cost class values must be non-decreasing along the pipeline.

    Concretely: confident zero-LLM steps run FIRST (cheapest + best
    when they hit); LLM steps run NEXT in cost order; the
    ZERO_LLM_FALLBACK tier runs LAST · it's a deterministic best-effort
    answer constructed from DB data for cases where every LLM step
    returned None (rate-limit, provider outage, etc).
    """
    ZERO_LLM_DB_HIT = 0       # pure DB · cache_hit, identity_guard, facts_det
    SINGLE_LLM = 1            # one LLM · full_doc_ctx
    MULTI_LLM = 2             # multi-LLM · agent ReAct loop
    ZERO_LLM_FALLBACK = 3     # deterministic last-resort · artifact_fallback


# ── Step context + signature ──────────────────────────────────────────────
@dataclass
class ChatContext:
    """Everything a step needs to decide its answer. Built once in
    post_message and threaded through the pipeline."""
    db: Session
    doc: Document
    text: str           # the user's question
    tenant_id: str
    doc_id_external: str
    user_msg_pk: int    # the just-persisted user message pk · for audit
    # M44.P9.2 · prior chat history in this thread, oldest-first.
    # List of {"role": "user"|"ai", "text": "..."} dicts. Empty for
    # the first message in a thread. Steps that talk to an LLM
    # (full_doc_ctx, rag_retrieval, agent) inject this into the
    # context so follow-up questions like 'and the second one?'
    # resolve correctly.
    history: list[dict] = field(default_factory=list)


# A step returns the AI ChatMessage to persist + return, or None to
# fall through to the next step. The step itself may DB-flush; the
# executor handles the commit + serialisation.
StepHandler = Callable[[ChatContext], ChatMessage | None]


@dataclass(frozen=True)
class ChatStep:
    name: str
    cost_class: CostClass
    handler: StepHandler
    description: str = ""
    # When set, the step only runs if `getattr(settings, enabled_flag)`
    # is truthy. Used for feature-flagged steps (e.g. agent_mode).
    enabled_flag: str | None = None


# ── Step implementations · small inline functions ─────────────────────────

def _step_cache_hit(ctx: ChatContext) -> ChatMessage | None:
    """Cosine-search reflexion_pairs for a sufficiently-similar prior
    question with reviewer 👍. Returns its cached answer · zero LLM."""
    s = get_settings()
    if not s.reflexion_cache_enabled:
        return None
    from app import reflexion_cache
    from app.documents_scope import get_current_owner_user_pk
    hit = reflexion_cache.lookup(
        ctx.db,
        tenant_id=ctx.tenant_id,
        question=ctx.text,
        doc_id_external=ctx.doc_id_external,
        owner_user_id=get_current_owner_user_pk(),
        similarity_threshold=s.reflexion_cache_threshold,
        min_helpful=s.reflexion_cache_min_helpful,
    )
    if hit is None:
        return None
    return _build_ai_msg(
        ctx, text=hit.answer, citations=[],
        meta=f"cache_hit · sim={hit.similarity:.2f} · reflex={hit.reflexion_pk}",
    )


def _step_identity_guard(ctx: ChatContext) -> ChatMessage | None:
    """When the question names a person who doesn't match the doc holder,
    refuse with a clear message · zero LLM. Defence against fuzzy-match
    leakage (Kalyani's DOB returned for a question about Rajesh)."""
    from app.services import doc_chat as svc
    ef = ctx.doc.extracted_fields or {}
    fields = ef.get("fields") if isinstance(ef, dict) else None
    if not fields:
        return None
    guard_answer = svc.check_identity_guard(ctx.text, fields)
    if not guard_answer:
        return None
    return _build_ai_msg(ctx, text=guard_answer, citations=[], meta="identity_guard")


def _step_facts_det(ctx: ChatContext) -> ChatMessage | None:
    """Pattern-match the question against ~17 known intents and read the
    answer directly from extracted_fields · zero LLM. The cheapest
    informative answer in the system."""
    # M46 · documents · list/table/breakdown requests must reach the LLM (which
    # emits a Markdown table the chat renders) — the single-value fact lookup
    # would mis-answer them with one field. Defer those.
    if get_settings().product == "documents":
        # (a) explicit list/table intent — always defer to the LLM (renders a table).
        _explicit = re.search(
            r"\b(table|list|all|every|each|breakdown|transactions?|line[ -]?items?|rows?|itemis|itemiz)\b",
            ctx.text, re.I)
        # (b) record-bearing docs (statements, invoices w/ line items): the single-
        #     fact fast path was hijacking record questions with ONE scalar field
        #     (e.g. credit-card "what's on this statement?" → just the credit limit).
        #     Defer when the question wants the rows OR is a broad overview, so the
        #     full-doc step surfaces the records.
        _rec = _wants_record_listing(ctx.doc, ctx.text) or (
            _doc_has_records(ctx.doc) and _RECORD_OVERVIEW_RE.search(ctx.text or ""))
        if _explicit or _rec:
            return None
        # (c) Phase 2a · if a user highlight is relevant to the question, defer to
        #     the LLM stages (full_doc_ctx / rag) which ground on highlights — the
        #     deterministic field lookup can't see highlight notes and would either
        #     mis-answer or pre-empt the highlight. Only fires on docs WITH a
        #     keyword-overlapping highlight, so docs without highlights are unaffected.
        try:
            _q = set(re.findall(r"[a-z0-9]{4,}", (ctx.text or "").lower()))
            if _q:
                for _hl in (_highlight_lines(ctx.db, ctx.doc_id_external) or []):
                    if _q & set(re.findall(r"[a-z0-9]{4,}", _hl.lower())):
                        return None
        except Exception:  # noqa: BLE001
            pass
    from app.services import doc_chat as svc
    ans, cites = svc.try_answer_from_facts_deterministic(ctx.doc, ctx.text)
    if not ans:
        return None
    svc._backfill_citation_bboxes(ctx.db, ctx.doc.pk, cites)
    return _build_ai_msg(ctx, text=ans, citations=cites or [], meta="facts_det")


_CHAT_CTX_BUDGET_CHARS = 200_000  # ~50K tokens · within Anthropic / OR safe range


def _format_history_block(history: list[dict], max_chars: int = 4000) -> str:
    """Render the prior thread for an LLM prompt. Newest-last (natural
    reading order). Truncates the oldest entries when over budget so
    the most recent exchanges always stay in context.

    Returns an empty string when history is empty so the prompt stays
    clean for first-turn questions.
    """
    if not history:
        return ""
    lines: list[str] = []
    # Walk newest-to-oldest, prepending; stop when budget exceeded.
    used = 0
    for msg in reversed(history):
        role = msg.get("role", "")
        text = (msg.get("text") or "").strip()
        if not text:
            continue
        prefix = "Reviewer" if role == "user" else "Assistant"
        snippet = f"{prefix}: {text[:600]}"
        if used + len(snippet) > max_chars:
            break
        lines.append(snippet)
        used += len(snippet)
    if not lines:
        return ""
    lines.reverse()
    return (
        "PRIOR CONVERSATION (oldest first; for context on follow-ups):\n"
        + "\n".join(lines)
        + "\n\n"
    )


def format_evidence_block(items, *, cap: int = 500, show_name: bool = False,
                          type_by_name: dict | None = None, empty: str = "",
                          prefix_lines: list[str] | None = None) -> str:
    """Render retrieval hits/chunks into the shared ``[E# · … · page N] snippet``
    evidence block used by every RAG surface (doc chat, workspace chat, the
    external `/v1/ask` + `/me/answer` APIs). One builder so the header format,
    snippet cap, and empty-fallback can't silently drift per call site.

    ``items`` are objects exposing ``.text`` and ``.page`` (and ``.document_name``
    when ``show_name``). The header is composed left-to-right: ``E#`` → optional
    ``· name`` → optional ``· type=…`` (only when ``type_by_name`` is given) →
    ``· page N``. ``prefix_lines`` are emitted before the ``[E*]`` lines (e.g.
    highlight ``[H*]`` lines). Returns ``empty`` when nothing renders.
    """
    lines: list[str] = list(prefix_lines or [])
    for i, it in enumerate(items, 1):
        snippet = " ".join((it.text or "").split())[:cap]
        head = f"E{i}"
        if show_name:
            head += f" · {it.document_name}"
            if type_by_name is not None:
                head += f" · type={type_by_name.get(it.document_name, 'document')}"
        head += f" · page {it.page}"
        lines.append(f"[{head}] {snippet}")
    return "\n\n".join(lines) or empty


def _pick_tier1_model(db: Session) -> str | None:
    """The tenant's first active tier-1 model id (same selection as
    llm_one_shot). Used so the critic runs on the reliable configured model
    rather than its hardcoded free default — important for the documents
    product where free tiers 429 under load."""
    try:
        from app.repositories import routing_config as rc_repo
        cfg = rc_repo.get(db) or {}
        tiers = cfg.get("tiers") or []
        t1 = next((t for t in tiers if t.get("id") == "t1"), tiers[0] if tiers else None)
        if t1:
            for m in t1.get("models") or []:
                if m and m.get("status", "active") == "active" and m.get("id"):
                    return m["id"]
    except Exception:  # noqa: BLE001
        pass
    return None


def _critique_refine(ctx: ChatContext, *, system: str, base_user_block: str,
                     draft: str, excerpts: list[str], meta_prefix: str) -> tuple[str, str]:
    """§3 · Critic self-correction. Reviews `draft` against the question +
    source excerpts; on a FAIL, re-prompts the same model with the critique to
    produce a corrected answer (bounded by critic_max_refines). Returns
    (final_answer, meta). Documents-product only; fail-open (any critic/LLM
    error keeps the current answer). No reflexion-cache writes here — that path
    needs the per-owner column (§4) before it's safe for the documents product."""
    s = get_settings()
    if s.product != "documents" or not is_enabled("documents_critic_enabled", True):
        return draft, meta_prefix
    max_refines = max(0, get_int("critic_max_refines", 1))
    if max_refines == 0:
        return draft, meta_prefix
    from app.agents.critic import critique as _critique_fn
    from app.services import doc_chat as svc
    ef = ctx.doc.extracted_fields or {}
    doc_summary = None
    if isinstance(ef, dict):
        doc_summary = (str(ef.get("doc_type") or "") + " · "
                       + str(ef.get("notes") or "")[:200]).strip(" ·") or None
    model = _pick_tier1_model(ctx.db)
    answer = draft
    trail: list[str] = []
    grounded_final = True   # fail-open: if the critic never runs / errors, treat as grounded
    refines_left = max_refines
    while True:
        try:
            crit = _critique_fn(
                question=ctx.text, draft=answer, source_excerpts=excerpts,
                doc_summary=doc_summary, doc_type=getattr(ctx.doc, "doc_type", None),
                model=model,
            )
        except Exception as e:  # noqa: BLE001 — fail-open
            log.warning("critic call failed: %s · keeping answer", e)
            break
        grounded_final = crit.passes   # reflects the answer we'll return
        if crit.passes or refines_left <= 0:
            break
        refines_left -= 1
        trail.append(crit.reason or "flagged")
        refine_user = (
            f"{base_user_block}\n\n"
            "REVIEWER CRITIQUE OF YOUR PREVIOUS DRAFT (you must address this):\n"
            f"Previous draft: {answer}\n"
            f"Critique reason: {crit.reason}\n"
            + (f"Critique suggestion: {crit.suggestion}\n" if crit.suggestion else "")
            + (f"Likely correct answer per critic: {crit.corrected_hint}\n" if crit.corrected_hint else "")
            + "\nProduce a corrected answer that addresses the critique. Same format rules."
        )
        try:
            revised = svc.llm_one_shot(ctx.db, system, refine_user,
                                       max_tokens=600, cache_system=True).strip()
        except Exception as e:  # noqa: BLE001 — fail-open
            log.warning("critic refine failed: %s · keeping prior answer", e)
            break
        if not revised:
            break
        answer = revised
    if trail:
        meta_prefix = f"{meta_prefix} · critic-refined×{len(trail)}: " + "; ".join(t[:50] for t in trail)
    # Grounding-gate abstention · when the answer is STILL ungrounded after refines
    # and strict mode is on, refuse rather than return a shaky answer. Uses the
    # critic that already ran — independent of retrieval score scale.
    if not grounded_final and s.chat_abstain_on_ungrounded:
        from app import abstention
        return abstention.refusal_message(), f"{meta_prefix} · insufficient_evidence"
    return answer, meta_prefix


def _highlight_lines(db, doc_id_external: str, *, limit: int = 8) -> list[str]:
    """Phase 2a · the caller's OWN highlights for this doc, as evidence lines.
    Owner-scoped (the annotations repo filters to the caller). Empty on error."""
    out: list[str] = []
    try:
        from app.repositories import annotations as _arepo
        for j, a in enumerate((_arepo.list_for_doc(db, doc_id_external) or []), start=1):
            t = " ".join((a.get("text") or "").split())[:600]
            note = (a.get("note") or "").strip()
            if not t and not note:
                continue
            line = f"[H{j} · YOUR HIGHLIGHT · page {a.get('page')}] {t}"
            if note:
                line += f"  (your note: {note})"
            out.append(line)
            if len(out) >= limit:
                break
    except Exception as e:  # noqa: BLE001
        log.warning("highlight grounding skipped: %s", e)
    return out


def _citations_from_json_markers(ctx: ChatContext, answer: str) -> tuple[str, list[dict]]:
    """Map the model's 【JSON:field】 markers to clickable citations (the field's page via
    field_bboxes) and strip the raw markers from the display text. Falls back to the extractor's
    source pages (chunk_refs) when the cited fields have no located bbox."""
    import re as _re
    from app.services import doc_chat as svc
    ef = ctx.doc.extracted_fields or {}
    field_bboxes = ef.get("field_bboxes") or {}
    fields = [m.strip() for m in _re.findall(r"【JSON:([^】]+)】", answer)]
    out: list[dict] = []
    seen: set = set()
    for fh in fields:
        for c in svc._build_citations_from_extractor(field_bboxes, [], field_hint=fh):
            key = (c.get("page"), c.get("fieldName"))
            if key not in seen:
                out.append(c)
                seen.add(key)
        if len(out) >= 5:
            break
    if not out:  # cited fields had no bbox (or no markers) → cite the extractor's source pages
        out = svc._build_citations_from_extractor(field_bboxes, ef.get("chunk_refs") or [], None)
    # Backfill missing bboxes from the chunks table (stale PKs from re-ingestion
    # are resolved via document+page fallback inside the helper).
    if out:
        svc._backfill_citation_bboxes(ctx.db, ctx.doc.pk, out)
    clean = _re.sub(r"\s*【JSON:[^】]+】", "", answer).strip()
    return (clean or answer), out


_THINKING_DIRECTIVE = (
    "\n\nFORMAT: FIRST output your reasoning as 2-4 SHORT steps (how you located the answer "
    "in the document), each on its own line starting with '- ', wrapped exactly between a "
    "line '[[THINKING]]' and a line '[[/THINKING]]'. THEN output the answer following ALL the "
    "rules above. The thinking block is shown to the user separately — keep it brief and do "
    "not repeat it in the answer."
)
_THINKING_RE = re.compile(r"\[\[THINKING\]\](.*?)\[\[/THINKING\]\]", re.S)


def _split_thinking(text: str) -> tuple[list[str], str]:
    """Pull an optional [[THINKING]]…[[/THINKING]] block out of a model reply. Returns
    (steps, answer). Fallback-safe: no block → ([], original text)."""
    if not text:
        return [], text
    m = _THINKING_RE.search(text)
    if not m:
        return [], text.strip()
    steps = [ln.strip(" -•\t") for ln in m.group(1).splitlines() if ln.strip(" -•\t")]
    answer = (text[:m.start()] + text[m.end():]).strip()
    return steps[:6], answer


def _step_full_doc_ctx(ctx: ChatContext) -> ChatMessage | None:
    """Send the WHOLE doc markdown to a single LLM call · Claude-attachment
    style. Beats the agent's multi-step loop for any doc that fits in
    the context budget. On LLM failure, returns None so the next step
    (agent) gets a turn."""
    from app.services import doc_chat as svc
    _s = get_settings()
    _docs_mode = _s.product == "documents" and _s.documents_agentic_chat
    art = ctx.db.scalar(
        select(DocumentArtifact).where(DocumentArtifact.document_pk == ctx.doc.pk)
    )
    # M46 · Documents · don't depend on a materialized artifact (the materializer
    # 429s under load). Build the full-doc context from the doc text + the rich
    # extracted fields, so the reliable single-shot path is ALWAYS available.
    full_md = None
    if art is not None and art.full_text_md and art.processing_strategy in ("full", "reduced"):
        full_md = art.full_text_md
    elif _docs_mode:
        full_md = svc.doc_text_excerpt(ctx.db, ctx.doc.pk, max_chars=_CHAT_CTX_BUDGET_CHARS)
    if not full_md:
        return None
    if len(full_md) > _CHAT_CTX_BUDGET_CHARS:
        return None
    art_struct = art.structured_json if art is not None else None
    if not art_struct and _docs_mode:
        ef = ctx.doc.extracted_fields or {}
        art_struct = ef.get("fields") if isinstance(ef.get("fields"), dict) else ef
    # M44.P5.2 · brutally-tight system prompt, modeled on xpenseaiq's
    # chat tab that the user benchmarked us against. The rules force the
    # model into the right answer shape · one-line value for "how much",
    # numbered steps for procedure, table for list-of-line-items. No
    # filler. The materialized JSON (when present) helps the model lock
    # onto exact values instead of paraphrasing them.
    system = (
        "You are DocAIQ — document audit assistant. Be precise. No filler.\n\n"
        "RULES:\n"
        "  · Single-value question (how much / when / who / what number) → "
        "ONE LINE answer. Quote the exact value from the document. No preamble.\n"
        "  · MULTI-PART question (asks for two or more things) → answer EVERY "
        "part, one short labelled line each. Never drop a part.\n"
        "  · List question (parties / line items / signatories / transactions) → "
        "markdown table or short bullet list. Quote exact values.\n"
        "  · Yes/No question → one word + one supporting line of evidence.\n"
        "  · Summarize / overview → max 3 sentences.\n"
        "  · Never invent data. If the document doesn't say, reply: "
        "'Not in this document.'\n"
        "  · REFERENCE RANGES: whenever your answer includes a reference / normal / "
        "desirable / optimal range for a value on a lab / medical / test report — whether "
        "the user asked to compare it, or only to show / share the result — report the "
        "measured value and printed range exactly, do NOT declare the value normal, "
        "abnormal, good or bad, and ALWAYS append one short line: on scanned reports a "
        "printed range can be mis-aligned to a neighbouring test, so verify against the "
        "original document.\n"
        "  · Never add commentary about what you're doing or why."
    )
    # When the doc carries a records/transactions array and the question wants the
    # rows, force a COMPLETE table (the summarize-in-3-sentences rule was dropping
    # rows for statements). Raise the token budget so all rows fit.
    _list_records = _wants_record_listing(ctx.doc, ctx.text)
    _max_tokens = 600
    if _list_records:
        system += (
            "\n\nIMPORTANT: this document contains a transactions/records table and "
            "the question is about it. Output EVERY row as a markdown table (date, "
            "description, amount, …). Do NOT summarize, group, or omit any row, and "
            "do NOT apply the 'overview → 3 sentences' rule here."
        )
        _max_tokens = 2000
    # Include structured JSON if available · the model anchors on typed
    # values more reliably when both are present.
    struct_json_block = ""
    if art_struct:
        import json as _json
        struct_json_block = (
            f"\n\nSTRUCTURED JSON (extracted fields incl. records/transactions — "
            f"prefer these for exact values):\n{_json.dumps(art_struct, indent=2)[:12000]}\n"
            "When a value in your answer comes from one of these fields, tag it inline with "
            "【JSON:field_name】 using the exact field key — this links the value to its source "
            "region on the document and is stripped from the displayed answer.\n"
        )
    history_block = _format_history_block(ctx.history)
    # M50 · split the prompt so the STABLE doc block is a cache_control prefix
    # (cached across turns ~90% cheaper) and only the varying history+question
    # is re-sent. Identical content + model → no quality change.
    doc_block = (
        f"Document: {ctx.doc.name}\n"
        f"{struct_json_block}\n"
        f"FULL DOCUMENT TEXT (markdown):\n\n{full_md}"
    )
    # Phase 2a · the user's own highlights/notes (may add info not in the body).
    _hl = _highlight_lines(ctx.db, ctx.doc_id_external)
    if _hl:
        doc_block += ("\n\nYOUR HIGHLIGHTS (regions/notes the user marked as important):\n"
                      + "\n".join(_hl))
        system += ("\n  · Excerpts under YOUR HIGHLIGHTS are the user's own marked regions/notes"
                   " — treat them as authoritative context (they may add info not in the body).")
    question_block = f"{history_block}Current question: {ctx.text}"
    # Ask the model to show its reasoning as a strippable [[THINKING]] block (rendered as a
    # 'Thinking' disclosure in the UI). Added to `system` so the cached doc prefix is untouched.
    system += _THINKING_DIRECTIVE
    # _critique_refine re-uses this combined block; keep it whole for that path.
    user_block = doc_block + "\n\n" + question_block
    try:
        answer = svc.llm_one_shot(
            ctx.db, system, question_block, max_tokens=_max_tokens, cache_system=True,
            cache_prefix=doc_block,
        ).strip()
    except Exception as e:  # noqa: BLE001
        log.warning("full_doc_ctx failed: %s · falling through", e)
        return None
    if not answer:
        return None
    thinking, answer = _split_thinking(answer)
    # §3 · self-correct against the doc text before returning. Give the critic
    # a few windows of the doc (it caps at 6 × 1200 chars) rather than just the head.
    excerpts = [full_md[i:i + 1200] for i in range(0, min(len(full_md), 7200), 1200)]
    answer, meta = _critique_refine(
        ctx, system=system, base_user_block=user_block, draft=answer,
        excerpts=excerpts or [full_md[:1200]], meta_prefix="full_doc_ctx",
    )
    # The refine pass runs with the same system prompt, so it may re-emit a THINKING block —
    # strip it again and keep whichever reasoning we captured.
    thinking2, answer = _split_thinking(answer)
    thinking = thinking or thinking2
    # Turn the model's 【JSON:field】 markers into clickable page citations (was: citations=[],
    # so full-doc answers never showed a source ref) and strip the raw markers from the text.
    clean, citations = _citations_from_json_markers(ctx, answer)
    return _build_ai_msg(ctx, text=clean, citations=citations, meta=meta, thinking=thinking)


def _step_artifact_fallback(ctx: ChatContext) -> ChatMessage | None:
    """LAST-RESORT zero-LLM answer when every LLM step missed (provider
    429, network outage, agent stuck). Reads the materialized doc memory
    (`document_artifacts`) and returns a focused answer.

    Tuned for concision after user benchmarked us against xpenseaiq-v5:
      · Summary request ("summarize", "overview") → just summary_long
      · Single-value question ("how much", "when", "what is the X") →
        the ONE most-relevant line from full_text_md, plus structured
        JSON value when available
      · No match → terse "Couldn't find that" with the summary as
        backup context

    Cost class: ZERO_LLM_FALLBACK · runs LAST in the pipeline.
    """
    art = ctx.db.scalar(
        select(DocumentArtifact).where(DocumentArtifact.document_pk == ctx.doc.pk)
    )
    if art is None or (not art.summary_long and not art.full_text_md):
        return None

    import re as _re

    q_lower = (ctx.text or "").lower()
    is_summary_request = any(
        kw in q_lower for kw in
        ("summari", "overview", "what is this document", "what's this document",
         "tell me about", "describe this")
    )

    # ── Summary request · just return summary_long ────────────────────
    if is_summary_request and art.summary_long:
        return _build_ai_msg(
            ctx, text=art.summary_long, citations=[], meta="artifact_fallback",
        )

    # ── Single-value or list question · find the best matching line ───
    stopwords = {
        "the", "a", "an", "is", "are", "was", "were", "be", "been",
        "what", "whats", "when", "where", "who", "how", "much", "many",
        "this", "that", "these", "those", "of", "in", "on", "at", "to",
        "for", "with", "and", "or", "but", "do", "does", "did", "can",
        "could", "would", "should", "have", "has", "had", "tell", "me",
        "please", "show", "give", "check", "review", "explain", "list",
    }
    tokens = [
        t.lower() for t in _re.findall(r"[A-Za-z][A-Za-z0-9-]{2,}", ctx.text)
        if t.lower() not in stopwords and len(t) >= 3
    ]

    matched_lines: list[str] = []
    if art.full_text_md and tokens:
        lines = [ln.strip() for ln in art.full_text_md.split("\n") if ln.strip()]
        scored: list[tuple[int, int, int, str]] = []
        for i, ln in enumerate(lines):
            ll = ln.lower()
            score = sum(1 for t in tokens if t in ll)
            # Boost lines with monetary / numeric values when the
            # question contains a "how much / amount / total / fee /
            # price" hint · those are usually the answer.
            if score > 0 and any(h in q_lower for h in
                ("how much", "amount", "total", "fee", "price", "cost",
                 "value", "due", "balance", "rate")):
                if _re.search(r"[\$£€¥₹]|\d{1,3}(?:[,]?\d{3})*(?:\.\d+)?|sgd|usd|eur|inr|gbp|aed",
                              ll):
                    score += 2
            if score > 0:
                scored.append((-score, i, i, ln[:300]))
        scored.sort()
        seen: set[str] = set()
        for _, _, idx, ln in scored[:5]:
            key = ln.lower()[:80]
            if key in seen:
                continue
            seen.add(key)
            # Heading-like match · grab the next 1-2 content lines, where
            # the actual value typically lives (e.g. "### Buyer (Bill to)"
            # → next line has the buyer name). Stops at the next
            # heading / table delimiter.
            is_heading = ln.startswith("#") or ln.endswith(":") or ln.startswith("**") and ln.endswith("**")
            if is_heading and idx + 1 < len(lines):
                appended = [ln]
                for j in range(idx + 1, min(idx + 4, len(lines))):
                    nxt = lines[j]
                    if nxt.startswith("#") or nxt.startswith("---") or nxt.startswith("|---"):
                        break
                    appended.append(nxt)
                    if len(appended) >= 3:
                        break
                matched_lines.append(" · ".join(appended))
            else:
                matched_lines.append(ln)
            if len(matched_lines) >= 3:
                break

    # Compose · concise · no preamble · no banner.
    if matched_lines:
        # Strip markdown table pipes for cleaner one-line output unless
        # there are multiple lines (a table genuinely helps for lists).
        if len(matched_lines) == 1:
            ln = matched_lines[0]
            # Strip markdown table delimiters and HTML breaks
            ln = _re.sub(r"^\|\s*|\s*\|\s*$", "", ln)
            ln = ln.replace("<br>", " · ").replace("|", " · ")
            ln = _re.sub(r"\s+", " ", ln).strip()
            return _build_ai_msg(
                ctx, text=ln, citations=[], meta="artifact_fallback",
            )
        # Multiple lines · render as bullet list, no header
        return _build_ai_msg(
            ctx,
            text="\n".join(f"• {ln}" for ln in matched_lines),
            citations=[],
            meta="artifact_fallback",
        )

    # No keyword match — be honest about it, show summary as context
    if art.summary_long:
        return _build_ai_msg(
            ctx,
            text=(
                "Not found in the indexed text. Here's the document summary "
                "for context:\n\n" + art.summary_long
            ),
            citations=[], meta="artifact_fallback",
        )
    return _build_ai_msg(
        ctx, text="Not found in this document.",
        citations=[], meta="artifact_fallback",
    )


def _step_rag_retrieval(ctx: ChatContext) -> ChatMessage | None:
    """Hybrid BM25 + cosine + reranker retrieval over document_chunks,
    then ONE LLM call with the top chunks as context.

    Sits AFTER full_doc_ctx (which handles small docs in full) and
    BEFORE the agent (which is a 1-8 LLM ReAct loop). The RAG path is
    the right answer for:
      · Docs too big for full_doc_ctx (>200K chars in the artifact)
      · Open-ended questions that don't need multi-step reasoning
      · Cases where the agent would just call search_chunks → answer

    Cheaper than the agent · more focused than full_doc_ctx on a big
    doc. Cost class: SINGLE_LLM.
    """
    from sqlalchemy import select as _select
    from app import retrieval
    from app.orm import DocumentChunk
    from app.services import doc_chat as svc

    # Skip when no chunks exist (e.g. doc still ingesting)
    chunks_count = ctx.db.scalar(
        _select(DocumentChunk).where(DocumentChunk.document_pk == ctx.doc.pk).limit(1)
    )
    if chunks_count is None:
        return None

    # Phase 2a · the user's own highlights for this doc (owner-scoped), so we can
    # ground on them even if hybrid retrieval returns nothing.
    hl_lines = _highlight_lines(ctx.db, ctx.doc_id_external)

    # Retrieve top-K · always include intro (page 1) since most doc
    # metadata sits there.
    hits = retrieval.retrieve(ctx.db, ctx.text, top_k=8, doc_id_external=ctx.doc_id_external)
    if not hits and not hl_lines:
        return None

    intro = ctx.db.scalars(
        _select(DocumentChunk)
        .where(DocumentChunk.document_pk == ctx.doc.pk)
        .order_by(DocumentChunk.chunk_index)
        .limit(2)
    ).all()

    seen_pks: set[int] = set()
    evidence: list[DocumentChunk] = []
    for c in intro:
        if c.pk not in seen_pks:
            evidence.append(c)
            seen_pks.add(c.pk)
    for h in hits:
        if h.chunk_pk in seen_pks:
            continue
        ch = ctx.db.scalar(_select(DocumentChunk).where(DocumentChunk.pk == h.chunk_pk))
        if ch is not None:
            evidence.append(ch)
            seen_pks.add(ch.pk)
        if len(evidence) >= 10:
            break

    # Compose evidence block · [H*] highlights first (Phase 2a), then [E*] chunks.
    evidence_block = format_evidence_block(
        evidence, cap=600, empty="(no evidence retrieved)", prefix_lines=list(hl_lines))

    system = (
        "You are DocAIQ — document audit assistant. Be precise. No filler.\n\n"
        "RULES:\n"
        "  · Single-value question → ONE LINE. Quote the exact value.\n"
        "  · List question → markdown table or short bullet list.\n"
        "  · Yes/No → one word + supporting evidence in one line.\n"
        "  · Summarize → max 3 sentences.\n"
        "  · Excerpts tagged YOUR HIGHLIGHT are regions the user marked as "
        "important — prioritize them when they're relevant to the question.\n"
        "  · Use ONLY the evidence excerpts below. If the answer isn't "
        "in them, reply: 'Not found in the retrieved evidence.'\n"
        "  · Never invent. Never explain what you're doing."
    ) + _THINKING_DIRECTIVE
    history_block = _format_history_block(ctx.history)
    user_block = (
        f"{history_block}"
        f"Document: {ctx.doc.name}\n\n"
        f"Evidence excerpts (retrieved via hybrid BM25 + cosine + reranker):\n\n"
        f"{evidence_block}\n\n"
        f"Current question: {ctx.text}"
    )
    try:
        answer = svc.llm_one_shot(
            ctx.db, system, user_block, max_tokens=500, cache_system=True,
        ).strip()
    except Exception as e:  # noqa: BLE001
        log.warning("rag_retrieval failed: %s · falling through", e)
        return None
    if not answer:
        return None
    thinking, answer = _split_thinking(answer)
    # §3 · self-correct against the retrieved evidence chunks before returning.
    answer, meta = _critique_refine(
        ctx, system=system, base_user_block=user_block, draft=answer,
        excerpts=[(c.text or "")[:1200] for c in evidence[:6]], meta_prefix="rag_retrieval",
    )
    thinking2, answer = _split_thinking(answer)
    thinking = thinking or thinking2
    # Persist citations so the UI can render chunk-page markers.
    # Forward the stored chunk bbox so PdfDocumentViewer draws a tight gold
    # box (precise coord path) instead of falling back to fuzzy text-search.
    citations = [
        {"chunkPk": int(c.pk), "page": int(c.page),
         "bbox": c.bbox if c.bbox else None,
         "quote": (c.text or "")[:200]}
        for c in evidence[:5]
    ]
    return _build_ai_msg(ctx, text=answer, citations=citations, meta=meta, thinking=thinking)


def _step_agent(ctx: ChatContext) -> ChatMessage | None:
    """Tool-using ReAct agent · max 8 steps · for complex queries that
    need multi-step reasoning or tool dispatch (search_chunks +
    validate_id_format + cross_doc_search etc).

    If the agent hits MAX_STEPS without converging OR returns the
    'could not produce / converge' fallback string, we delete its
    message + traces and return None so the next step (artifact_fallback)
    gets a turn with a useful deterministic answer."""
    # M46 · run for the documents product (agentic chat) OR when the operator
    # flips on agent mode in audit. Gated here (not via enabled_flag) so audit
    # stays unchanged while documents always gets the agent.
    # P2 · cloud-only — OSS deployments fall through to RAG.
    from app.license import is_cloud
    _s = get_settings()
    if not (_s.agent_mode_enabled or (is_cloud() and _s.product == "documents" and _s.documents_agentic_chat)):
        return None
    try:
        from app.agents import document_agent
        result = document_agent.run(ctx.db, ctx.doc, ctx.text, tenant_id=ctx.tenant_id)
    except Exception as e:  # noqa: BLE001
        log.warning("agent failed: %s · falling through", e)
        return None

    msg = result.chat_message
    text = (msg.text or "").strip()
    # The agent's well-known unhelpful outputs · these are the
    # forced-terminate cases where the loop ran but couldn't synthesize.
    is_unhelpful = (
        (msg.meta or "").endswith("forced_terminate")
        or text.startswith("The agent could not produce")
        or text.startswith("The agent could not converge")
        or text.startswith("(agent produced no parseable answer)")
    )
    if is_unhelpful:
        # Delete the message · CASCADE on agent_traces drops the traces too.
        # Avoids persisting useless rows AND lets the pipeline fall through
        # to artifact_fallback for an actually-useful deterministic answer.
        log.info(
            "agent · unhelpful output detected (meta=%r) · dropping msg pk=%d "
            "to let pipeline fall through",
            msg.meta, msg.pk,
        )
        ctx.db.delete(msg)
        ctx.db.flush()
        return None

    # Surface the agent's ReAct thoughts as the inline "Thinking" disclosure — same UX as the
    # other chat paths. The full step-by-step trace (thought · action · observation) stays
    # available via "Show reasoning" (the /trace endpoint over agent_traces).
    thoughts = [s.thought.strip() for s in result.steps
                if getattr(s, "thought", None) and s.thought.strip()]
    if thoughts:
        msg.trace = thoughts[:8]
        ctx.db.flush()
    return msg


# ── THE PIPELINE · order is the source of truth ───────────────────────────
# Adding a step: pick the right slot for its cost class. The boot-time
# validator catches misordering before the container accepts traffic.
PIPELINE: tuple[ChatStep, ...] = (
    ChatStep(
        "cache_hit", CostClass.ZERO_LLM_DB_HIT, _step_cache_hit,
        "Reflexion cache · cosine search for prior thumbs-up answer.",
    ),
    ChatStep(
        "identity_guard", CostClass.ZERO_LLM_DB_HIT, _step_identity_guard,
        "Refuse questions about wrong-person before fuzzy-match leaks.",
    ),
    ChatStep(
        "facts_det", CostClass.ZERO_LLM_DB_HIT, _step_facts_det,
        "Regex intent → dict lookup over extracted_fields.",
    ),
    ChatStep(
        "full_doc_ctx", CostClass.SINGLE_LLM, _step_full_doc_ctx,
        "Whole-doc context in one LLM call · Claude-attachment style.",
    ),
    ChatStep(
        "rag_retrieval", CostClass.SINGLE_LLM, _step_rag_retrieval,
        "Hybrid BM25 + cosine + reranker · single LLM call over top "
        "chunks. Handles docs too big for full_doc_ctx.",
    ),
    ChatStep(
        "agent", CostClass.MULTI_LLM, _step_agent,
        "Document Agent · ReAct loop with tools · for complex queries. "
        "Gated INSIDE the handler (agent_mode_enabled OR documents product).",
    ),
    ChatStep(
        "artifact_fallback", CostClass.ZERO_LLM_FALLBACK, _step_artifact_fallback,
        "Last-resort · keyword excerpts from materialized markdown + "
        "summary, when all LLM-spending steps returned None.",
    ),
    # NOTE · the legacy single-shot + retrieval paths stay in
    # post_message after this pipeline as the truly-exhausted fallback.
)


# ── Invariant + executor ──────────────────────────────────────────────────
def validate_pipeline() -> None:
    """Boot-time assertion · cost class values must be non-decreasing
    along the PIPELINE. This catches BOTH original failure modes:

      · 'agent before facts_det' (MULTI_LLM=2 before ZERO_LLM_DB_HIT=0)
      · 'artifact_fallback before agent' (FALLBACK=3 before MULTI_LLM=2)

    Raises AssertionError on violation · uvicorn logs the error and
    the container's healthcheck never goes green. Better to crash on
    boot than serve wrong-cost-ordered answers.
    """
    for i in range(1, len(PIPELINE)):
        prev = PIPELINE[i - 1]
        cur = PIPELINE[i]
        if cur.cost_class.value < prev.cost_class.value:
            raise AssertionError(
                f"chat_pipeline ordering violation · step '{cur.name}' "
                f"({cur.cost_class.name}={cur.cost_class.value}) appears AFTER "
                f"'{prev.name}' ({prev.cost_class.name}={prev.cost_class.value}). "
                f"Cost class values must be non-decreasing along the pipeline. "
                f"Reorder the PIPELINE tuple in app/services/chat_pipeline.py."
            )
    log.info(
        "chat_pipeline · %d steps validated · cost order: %s",
        len(PIPELINE),
        " → ".join(f"{s.name}({s.cost_class.name})" for s in PIPELINE),
    )


def execute_pipeline(ctx: ChatContext) -> ChatMessage | None:
    """Walk the PIPELINE in order. Return the first step's answer
    that is non-None. Returns None if every step missed · the caller
    then runs the legacy retrieval fallback.

    M44.P9.1 · For every winning step EXCEPT cache_hit (which itself
    serves a prior reflexion row) AND identity_guard (refusal · no
    reusable answer), persist a reflexion_pairs row so:
      · reviewer 👍 lands on a real row that can be retrieved later
      · future similar questions hit the cache via cosine search
    """
    s = get_settings()
    for step in PIPELINE:
        if step.enabled_flag and not getattr(s, step.enabled_flag, False):
            continue
        try:
            result = step.handler(ctx)
        except Exception as e:  # noqa: BLE001
            log.warning("pipeline step '%s' raised: %s · trying next", step.name, e)
            continue
        if result is not None:
            log.info(
                "chat_pipeline · '%s' answered (cost=%s)",
                step.name, step.cost_class.name,
            )
            _persist_reflexion_if_warranted(ctx, step.name, result)
            # Chat-faithfulness corpus — snapshot the answer for consented free users
            # (gated inside). A SAVEPOINT isolates it so a capture hiccup can never
            # affect the answer the user gets.
            try:
                with ctx.db.begin_nested():
                    from app.services import faithfulness_corpus
                    faithfulness_corpus.capture_case(ctx, result)
            except Exception as e:  # noqa: BLE001
                log.warning("faithfulness capture failed (non-fatal): %s", e)
            return result
    log.info("chat_pipeline · all steps missed · caller falls back")
    return None


def _persist_reflexion_if_warranted(
    ctx: ChatContext, step_name: str, msg: ChatMessage,
) -> None:
    """Persist a reflexion_pairs row so reviewer feedback (👍/👎) has a
    target to mutate AND future similar questions can hit the cache.

    Skipped for:
      · cache_hit · already from a prior reflexion row · re-persisting
        would double-count
      · identity_guard · refusal answer, not generally reusable
      · agent · the agent path already persists its own reflexion via
        document_agent.run() with richer trace metadata · don't double up
    """
    if step_name in ("cache_hit", "identity_guard", "agent"):
        return
    try:
        from app.embeddings import embed as _embed_fn
        from app.orm import ReflexionPair

        from app.documents_scope import get_current_owner_user_pk
        [q_vec] = _embed_fn([ctx.text])
        ctx.db.add(ReflexionPair(
            tenant_id=ctx.tenant_id,
            question=ctx.text,
            question_embed=q_vec,
            draft_answer=msg.text,
            critique=f"answered via {step_name}",
            final_answer=msg.text,
            doc_id_external=ctx.doc_id_external,
            owner_user_id=get_current_owner_user_pk(),
            iterations=1,
            passed_on_first=True,
        ))
        ctx.db.flush()
    except Exception as e:  # noqa: BLE001
        log.warning(
            "reflexion persist failed for step=%s (non-fatal): %s",
            step_name, e,
        )


# ── Helpers ───────────────────────────────────────────────────────────────
def _build_ai_msg(
    ctx: ChatContext,
    *,
    text: str,
    citations: list,
    meta: str,
    confidence: float | None = None,
    thinking: list | None = None,
) -> ChatMessage:
    """Construct + persist the AI message row. The executor will
    commit; we just add+flush so the FK ordering is right."""
    msg = ChatMessage(
        tenant_id=ctx.tenant_id,
        requirement_id_external=None,
        doc_id_external=ctx.doc_id_external,
        role="ai",
        text=text,
        confidence=confidence,
        citations=citations,
        meta=meta,
        trace=(thinking or None),  # inline reasoning steps → rendered as a 'Thinking' disclosure
    )
    ctx.db.add(msg)
    ctx.db.flush()
    return msg
