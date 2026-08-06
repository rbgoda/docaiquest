"""M44.P12 · Overall-documents chat (cross-document Q&A).

The single-document chat (`services/doc_chat.py` + `chat_pipeline`) answers
questions about ONE document. This service answers questions that span a whole
*set* of documents — today, all of a vendor's documents in the Documents tab
("which policies expire this year?", "compare the two insurance certs",
"what's the total across all the invoices?").

Design notes
------------
* **Scope = a doc-pk set.** The caller passes a `vendor_pk`; we resolve it to
  the ready documents that belong to that vendor (plus tenant-shared docs with
  `vendor_pk IS NULL`, matching how the Documents tab lists them). Retrieval is
  restricted to that set via `retrieval.retrieve(..., doc_pks=...)`.
* **Thread anchor = `workspace_key`.** Messages persist on `chat_messages` with
  `workspace_key='vendor:<pk>'` and both other anchors NULL. One shared thread
  per vendor (like doc-chat, not per-user).
* **Citations are doc-attributed.** Each evidence chunk carries its source
  document id + name so the UI can say "from Insurance_Cert.pdf, page 2".
* **Single LLM call.** RAG over the doc set → one `llm_one_shot`. We skip the
  full document-agent loop and the reflexion cache for v1: the cache is keyed
  by `doc_id_external` and mixing a cross-doc answer into per-doc cache rows
  would let a doc-A answer leak into a doc-B question. Conversation memory
  (last N turns) IS threaded so follow-ups resolve.
"""
from __future__ import annotations

import logging

import re

from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.documents_scope import get_current_owner_user_pk
from app.feature_flags import is_enabled
from app.license import is_cloud
from app.orm import ChatMessage, Document
from app.services import doc_chat as doc_chat_service
from app.services.chat_pipeline import _format_history_block
from app.services.workspace_handlers import (  # extracted deterministic handlers
    deterministic_answer, _is_broad_overview, _aggregate_overview, answer_name_query,
    answer_doc_type_query, owner_doc_types,
    _TYPE_SYNONYMS,  # still referenced here (_detect_list_request) + re-exported for api_v1 (wc._TYPE_SYNONYMS)
)

log = logging.getLogger("docaiq.workspace_chat")

_HISTORY_MAX = 8          # prior turns threaded into the prompt
_TOP_K = 12               # chunks retrieved across the doc set
_MAX_EVIDENCE = 12        # evidence excerpts shown to the LLM
_MAX_CITATIONS = 6        # citations persisted on the answer


def workspace_key(vendor_pk: int | None, group_id: int | None = None,
                  conv_id: str | None = None) -> str:
    # A1 · a per-group cross-doc thread (members share it) — no per-conversation split.
    if group_id is not None:
        return f"group:{group_id}"
    base = _workspace_key_user_or_vendor(vendor_pk)
    # Multiple saved conversations per user: an explicit conv id suffixes the base key
    # (`user:<pk>:c:<id>`). No id → the original base key, so existing threads keep working.
    if conv_id:
        cid = re.sub(r"[^A-Za-z0-9]", "", str(conv_id))[:40]
        if cid:
            return f"{base}:c:{cid}"
    return base


def _workspace_key_user_or_vendor(vendor_pk: int | None) -> str:
    """Anchor key for the cross-doc thread. `vendor:<pk>` scopes to one
    vendor's documents; `tenant` is reserved for a future all-tenant chat.

    M46 · in the documents product the thread is per-USER (`user:<pk>`) so two
    self-registered users in a shared documents stack never share a thread."""
    uid = get_current_owner_user_pk()
    if uid is not None:
        return f"user:{uid}"
    return f"vendor:{vendor_pk}" if vendor_pk else "tenant"


_LIST_RX = re.compile(r"\b(list|share|show|give me|display|table of|tabulate)\b", re.I)


def _detect_list_request(text: str) -> str | None:
    """'list/share/show <TYPE> [in table]' → the matched type phrase, else None.
    Longest phrase wins so 'bank statement' beats 'statement'."""
    t = (text or "").lower()
    if not _LIST_RX.search(t):
        return None
    for phrase in sorted(_TYPE_SYNONYMS, key=len, reverse=True):
        if phrase in t:
            return phrase
    return None


# M51 · the agentic loop is for things plain RAG can't do — ACTIONS (group /
# rename / tag / re-extract / sync) and STRUCTURED output (table / CSV / xlsx /
# export / compare / duplicates). Plain content questions ("bank details",
# "summary of X", "what's the total") stay on the proven RAG path, which gives
# fuller, better-grounded answers than the agent's tool snippets. High-precision
# on purpose: when in doubt, RAG.
_AGENT_INTENT_RX = re.compile(
    r"(creat\w*\s+(a\s+)?group|new\s+group|make\s+(a\s+)?group"
    r"|add\s+\w.*\bto\b.*\bgroup|put\s+\w.*\bgroup|move\s+.*\bgroup"
    r"|\brenam\w+|\bre-?name\b"
    r"|tag\s+\w.*\bas\b|\bset\s+tags?\b|\badd\s+tags?\b|\bremove\s+tags?\b|\buntag\w*"
    r"|\bre-?classif\w*|\bre-?extract\w*|\bre-?index\w*"
    r"|\bsync\b"
    r"|\bexport\w*|\bexcel\b|\bxlsx\b|\bworkbook\b|\bspreadsheet\b|\bcsv\b"
    r"|\btabulate\b|\btable\b"
    r"|\bcompare\b|side[-\s]?by[-\s]?side|\bversus\b"
    r"|\bduplicate\w*|\bdupe\w*)", re.I)
_AGENT_CONFIRM_RX = re.compile(
    r"^\s*(yes|yep|yeah|yup|confirm|confirmed|go ahead|proceed|do it|sure|"
    r"ok|okay|please do|sounds good|approve|approved)\b", re.I)


_PII_NAME_KEYS = ("name", "holder", "beneficiary", "recipient", "issuer", "payee",
                  "customer", "patient", "applicant", "insured", "nominee",
                  "drawer", "drawee", "sender", "vendor", "supplier")
_PII_CONTACT_KEYS = ("email", "phone", "mobile", "contact", "tel")
# Org / authority names are NOT personal PII — redacting them as [PERSON] only
# adds noise that degrades the model's reading of the evidence. Skip them.
_ORG_RX = re.compile(
    r"\b(inc|ltd|llc|llp|plc|pte|corp|co|gmbh|s\.?a\.?|department|dept|authority|"
    r"bureau|agency|government|govt|ministry|administration|customs|university|"
    r"college|hospital|clinic|bank|insurance|services|systems|technologies|"
    r"solutions|holdings|website|board|council|commission|office|homeland)\b",
    re.IGNORECASE)
_ORG_ROLES = ("issuer", "authority", "department", "agency", "bureau",
              "government", "bank", "company", "organization", "org", "employer")


def _pii_extra_terms(docs) -> list[tuple[str, str]]:
    """High-risk PII values from the in-scope docs' extracted fields — names,
    identifiers, account numbers, contacts — handed to the gateway redactor as
    Tier-2 `extra_terms` so they're masked in the evidence regardless of the
    surrounding text. Amounts/dates are EXCLUDED (the model must reason over
    them). No-op unless DOCAIQ_PII_REDACT_BEFORE_LLM is on."""
    terms: list[tuple[str, str]] = []
    seen: set[str] = set()

    def add(label: str, val) -> None:
        s = str(val).strip()
        # Don't redact org/authority names as a person — only real PII.
        if label == "person" and _ORG_RX.search(s):
            return
        if 3 <= len(s) <= 80 and s.lower() not in seen:
            seen.add(s.lower())
            terms.append((label, s))

    for d in docs:
        ef = d.extracted_fields or {}
        f = ef.get("fields") if isinstance(ef.get("fields"), dict) else ef
        if not isinstance(f, dict):
            continue
        ids = f.get("identifiers")
        if isinstance(ids, list):
            for it in ids:
                if isinstance(it, dict) and it.get("value"):
                    add("id", it["value"])
        # Names nested in a persons/parties list (role-labelled) — these also
        # ride in the structured-fields block, so they must be redactable.
        for listkey in ("persons", "parties", "people", "names", "entities"):
            lst = f.get(listkey)
            if isinstance(lst, list):
                for p in lst:
                    if isinstance(p, dict) and p.get("name"):
                        role = str(p.get("role") or "").lower()
                        if any(o in role for o in _ORG_ROLES):
                            continue  # org party (issuer/authority/bank), not personal PII
                        add("person", p["name"])
                    elif isinstance(p, str):
                        add("person", p)
        for k, v in f.items():
            if not isinstance(v, (str, int, float)):
                continue
            kl = k.lower()
            if any(n in kl for n in _PII_NAME_KEYS):
                add("person", v)
            elif any(c in kl for c in _PII_CONTACT_KEYS):
                add("contact", v)
            elif "account" in kl:
                add("account", v)
        if len(terms) >= 80:
            break
    return terms[:80]


def _structured_fields_block(docs, evidence) -> str:
    """Authoritative extracted fields for the docs that have retrieved evidence.
    The extractor understands FIELD ROLES (applicant vs emergency contact, total
    vs subtotal) — raw chunk retrieval doesn't — so feeding these in lets the
    model pick the RIGHT value instead of grabbing any matching string."""
    import json as _json
    ev_ids = {getattr(h, "document_id_external", None) for h in evidence}
    out = []
    for d in docs:
        if d.id_external not in ev_ids:
            continue
        ef = d.extracted_fields or {}
        f = ef.get("fields") if isinstance(ef.get("fields"), dict) else ef
        if not isinstance(f, dict) or not f:
            continue
        out.append(f"- {d.name} (type={d.doc_type or '?'}): "
                   f"{_json.dumps(f, ensure_ascii=False)[:1400]}")
        if len(out) >= 10:
            break
    if not out:
        return ""
    return ("STRUCTURED FIELDS (authoritative · extracted per document · these "
            "carry ROLE labels like applicant vs emergency contact — PREFER them "
            "for attribute/value questions):\n" + "\n".join(out) + "\n\n")


def _wants_agent(text: str, prior: list[dict] | None = None) -> bool:
    """True when the message is an ACTION / STRUCTURED request (→ agent), or a
    short confirmation of an action the agent just previewed."""
    if _AGENT_INTENT_RX.search(text or ""):
        return True
    # A bare "yes" only routes to the agent if the agent was mid-confirm.
    if _AGENT_CONFIRM_RX.search(text or "") and prior:
        for m in reversed(prior):
            if m.get("role") in ("ai", "assistant"):
                t = (m.get("text") or "").lower()
                return any(k in t for k in ("confirm", "reply yes", "preview", "proceed"))
    return False


def _matched_docs_by_type(docs: list[Document], phrase: str) -> list[Document]:
    fams = _TYPE_SYNONYMS.get(phrase, (phrase.replace(" ", "_"),))
    return [d for d in docs if any(f in (d.doc_type or "").lower() for f in fams)]


def _answer_type_listing(db: Session, tenant_id: str, wkey: str, phrase: str,
                         docs: list[Document]) -> dict:
    """Deterministic doc selection by classified type + LLM tabulation of their
    extracted fields. Correct recall (never misses the real doc) + correct type
    filter (never includes a doc that merely mentions an identifier)."""
    import json as _json
    if not docs:
        return _persist_ai(
            db, tenant_id, wkey,
            text=f"You have no **{phrase}** documents in your workspace.",
            citations=[], meta="workspace · type_listing_empty",
        )
    blocks = []
    for d in docs[:60]:
        ef = d.extracted_fields or {}
        f = ef.get("fields") if isinstance(ef.get("fields"), dict) else ef
        compact = _json.dumps(f, ensure_ascii=False)[:2500] if isinstance(f, dict) else "{}"
        blocks.append(f"Document: {d.name} (type={d.doc_type})\nExtracted fields: {compact}")
    system = (
        f"You are DocAIQuest. Tabulate the {phrase} documents below — these are ONLY the "
        f"workspace's {phrase} documents, already selected by their classified type, "
        "with their extracted fields. Output ONE markdown table, one row per "
        "document, with sensible columns for this kind (e.g. Document, Name, ID "
        "number, Date of birth). Use the extracted fields; write 'Not found' for a "
        "missing cell. Do NOT add documents or invent values. No preamble."
    )
    try:
        answer = (doc_chat_service.llm_one_shot(db, system, "\n\n".join(blocks), max_tokens=900,
                                                extra_terms=_pii_extra_terms(docs)) or "").strip()
    except Exception:  # noqa: BLE001
        answer = ""
    if not answer:  # deterministic fallback when the LLM is unavailable
        answer = (f"Your **{phrase}** documents:\n\n| Document | Type |\n|---|---|\n"
                  + "\n".join(f"| {d.name} | {d.doc_type} |" for d in docs[:60]))
    note = f"\n\n_Selected by document type — {len(docs)} {phrase} document(s) in your workspace._"
    cites = [{"docId": getattr(d, "id_external", None), "docName": d.name}
             for d in docs[:20] if getattr(d, "id_external", None)]
    return _persist_ai(db, tenant_id, wkey, text=answer + note, citations=cites, meta="workspace · type_listing")


def resolve_scope_docs(
    db: Session, tenant_id: str, vendor_pk: int | None,
    doc_ids: list[str] | None = None, limit: int | None = None,
    group_id: int | None = None,
) -> list[Document]:
    """The ready documents in scope for this workspace chat.

    Mirrors the Documents-tab listing: a vendor's own docs PLUS tenant-shared
    docs (`vendor_pk IS NULL`). Only `ingestion_status='ready'` docs have
    chunks to retrieve over, so unready/failed docs are excluded.

    When `doc_ids` is given (subset mode), the result is further restricted to
    those `id_external`s — but ALWAYS intersected with the vendor/ready scope,
    so a caller can never widen their reach by passing ids they can't see.
    """
    stmt = select(Document).where(
        Document.tenant_id == tenant_id,
        Document.ingestion_status == "ready",
    )
    if vendor_pk is not None:
        stmt = stmt.where(
            (Document.vendor_pk == vendor_pk) | (Document.vendor_pk.is_(None))
        )
    if group_id is not None:
        # A1 · group chat · scope to docs shared into this group (membership is
        # verified by the caller). Not owner-scoped — members see group docs.
        from app.orm import DocumentGroupShare
        in_group = select(DocumentGroupShare.document_pk).where(
            DocumentGroupShare.group_id == group_id)
        stmt = stmt.where(Document.pk.in_(in_group))
    else:
        # M46 · documents product · only the current user's own docs are in scope.
        uid = get_current_owner_user_pk()
        if uid is not None:
            stmt = stmt.where(Document.owner_user_id == uid)
    if doc_ids:
        stmt = stmt.where(Document.id_external.in_(list(doc_ids)))
    # Most-recent-first so a capped scope keeps the freshest documents.
    stmt = stmt.order_by(Document.pk.desc())
    if limit:
        stmt = stmt.limit(limit)
    return list(db.scalars(stmt).all())


def _history(db: Session, tenant_id: str, wkey: str, exclude_pk: int) -> list[dict]:
    """Last N messages in this workspace thread, oldest-first, for prompt
    context. Excludes the just-persisted user message and summary rows."""
    prior = db.scalars(
        select(ChatMessage)
        .where(
            ChatMessage.tenant_id == tenant_id,
            ChatMessage.workspace_key == wkey,
            ChatMessage.pk != exclude_pk,
            (ChatMessage.meta != "summary") | ChatMessage.meta.is_(None),
        )
        .order_by(ChatMessage.pk.desc())
        .limit(_HISTORY_MAX)
    ).all()
    return [{"role": m.role, "text": m.text} for m in reversed(prior)]


def get_thread(db: Session, tenant_id: str, vendor_pk: int | None,
               group_id: int | None = None, conv_id: str | None = None) -> dict:
    """Return the persisted cross-doc thread + the in-scope doc count."""
    wkey = workspace_key(vendor_pk, group_id, conv_id)
    msgs = db.scalars(
        select(ChatMessage)
        .where(
            ChatMessage.tenant_id == tenant_id,
            ChatMessage.workspace_key == wkey,
        )
        .order_by(ChatMessage.pk)
    ).all()
    docs = resolve_scope_docs(db, tenant_id, vendor_pk, group_id=group_id)
    return {
        "workspaceKey": wkey,
        "docCount": len(docs),
        "messages": [_msg_to_dict(m) for m in msgs],
    }


def clear_thread(db: Session, tenant_id: str, vendor_pk: int | None,
                 group_id: int | None = None, conv_id: str | None = None) -> int:
    """Delete every message in ONE workspace conversation — the chat 'Delete' action.
    Owner-scoped: `workspace_key` resolves to `user:<owner_pk>` in the documents
    product, so this only ever wipes the signed-in user's OWN thread (never
    touches documents — just the conversation). Returns the count removed."""
    wkey = workspace_key(vendor_pk, group_id, conv_id)
    res = db.execute(
        delete(ChatMessage).where(
            ChatMessage.tenant_id == tenant_id,
            ChatMessage.workspace_key == wkey,
        )
    )
    db.commit()
    return res.rowcount or 0


def list_threads(db: Session, tenant_id: str, vendor_pk: int | None,
                 group_id: int | None = None) -> list[dict]:
    """List this owner's saved cross-doc conversations (the base thread + every
    `:c:<id>` conversation), newest first, each with a title (its first user
    message) + message count + last-activity time. Powers the chat history picker."""
    base = workspace_key(vendor_pk, group_id)  # conv_id None → the base key
    rows = db.execute(
        select(ChatMessage.workspace_key, func.count(), func.max(ChatMessage.pk))
        .where(ChatMessage.tenant_id == tenant_id,
               or_(ChatMessage.workspace_key == base,
                   ChatMessage.workspace_key.like(base + ":c:%")))
        .group_by(ChatMessage.workspace_key)
    ).all()
    out: list[dict] = []
    for wk, cnt, max_pk in rows:
        conv = wk.split(":c:", 1)[1] if ":c:" in wk else None
        first = db.scalars(
            select(ChatMessage).where(
                ChatMessage.tenant_id == tenant_id,
                ChatMessage.workspace_key == wk,
                ChatMessage.role == "user",
            ).order_by(ChatMessage.pk).limit(1)
        ).first()
        last = db.get(ChatMessage, max_pk)
        title = ((first.text or "").strip()[:60] if first else "") or "New conversation"
        out.append({
            "conv": conv,
            "title": title,
            "count": int(cnt or 0),
            "updatedAt": (last.created_at.isoformat() if last and last.created_at else None),
        })
    out.sort(key=lambda t: t["updatedAt"] or "", reverse=True)
    return out


def _dedupe_citations(cites: list) -> list:
    """One chip per source document — collapse repeated citations (same docId) so the reader gets a
    clean source list, keeping the first (has the field/page for the pulse)."""
    out, seen = [], set()
    for c in cites or []:
        if not isinstance(c, dict):
            continue
        key = c.get("docId") or c.get("docName") or id(c)
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out


def _msg_to_dict(m: ChatMessage) -> dict:
    return {
        "id": m.pk,
        "role": m.role,
        "text": m.text,
        "citations": _dedupe_citations(m.citations),
        "confidence": m.confidence,
        "createdAt": m.created_at.isoformat() if m.created_at else None,
        "meta": m.meta,
        # M51 · agent step trace ("what the assistant did") + downloadables (CSV).
        "trace": m.trace or [],
        "artifacts": m.tools or [],
    }


_ACTION_RX = re.compile(
    r"\b(delete|remove|erase|wipe|destroy|purge|drop|clear)\b[\w\s]{0,20}"
    r"\b(document|doc|docs|file|files|everything|all|data|workspace)\b", re.I)


def _unsupported_action(text: str) -> str | None:
    """Destructive/mutating asks aren't done from chat (safety). Return a helpful pointer instead of
    a canned overview."""
    if _ACTION_RX.search(text or ""):
        return ("I can't delete or change your documents from chat — that's a safety boundary. To "
                "remove a document, open the document list and use **Archive / Delete** on it. I'm "
                "here to *answer questions* about your files — e.g. *“summarise my invoices”* or "
                "*“what's in my ID?”*")
    return None


def _general_assistant(question: str, prior: list | None) -> str | None:
    """Off-topic / general-knowledge fallback (weather, world facts, coding help) — the LLM answers
    from general knowledge instead of a dead 'not found'. The prompt makes the model self-gate: if
    the question is really about the user's documents it returns NEED_DOCUMENT and we keep the normal
    doc dead-end. No real-time/web access. Model is configurable (qwen default; DeepSeek/Gemini free
    via OpenRouter)."""
    s = get_settings()
    if not is_enabled("documents_general_fallback_enabled", True):
        return None
    from app.llm import gateway
    model = s.documents_general_fallback_model or s.strong_extract_model
    if "/" not in model:  # gateway routes by provider prefix
        model = f"dashscope/{model}"
    sys = (
        "You are DocAIQuest's assistant. DocAIQuest helps users with THEIR uploaded documents. Decide: if "
        "this question is about the user's personal or uploaded documents (their invoices, IDs, "
        "statements, resumes, or finding/summarising/comparing specific files), reply with EXACTLY "
        "'NEED_DOCUMENT' and nothing else. Otherwise answer the general question helpfully and "
        "concisely. You have NO real-time data (weather, news, live prices) and NO web/URL browsing; "
        "if asked for those, say so in one short line. After a general answer, add one line: "
        "\"That's outside your documents — I'm mainly here to help with your files.\"")
    msgs = [gateway.Message(role="system", content=sys)]
    for m in (prior or [])[-4:]:
        role = "user" if (m.get("role") == "user") else "assistant"
        msgs.append(gateway.Message(role=role, content=(m.get("text") or "")[:600]))
    msgs.append(gateway.Message(role="user", content=(question or "")[:1000]))
    try:
        r = gateway.call(model=model, messages=msgs, temperature=0.3, max_tokens=500)
        ans = (r.text or "").strip()
        if not ans or "NEED_DOCUMENT" in ans[:24].upper():
            return None
        return ans
    except Exception as e:  # noqa: BLE001
        logging.getLogger("docaiq.workspace").warning("general_assistant failed: %s", e)
        return None


# A follow-up leans on the prior turn: anaphora ('both', 'the second one', 'them')
# or a terse question that can't stand alone. Used to gate the (LLM) rewrite so
# standalone questions pay no latency.
_FOLLOWUP_RX = re.compile(
    r"\b(both|them|those|these|they|the two|the three|the first|the second|the third|"
    r"the other|the last|the latter|the former|that one|this one|ones?|above|below|"
    r"previous|earlier|prior|same|list them|show them|which ones?)\b", re.I)


# A question about what's ABSENT ("what documents am I missing", "what do I need that I don't
# have") must NOT be answered by listing what the user HAS — it needs reasoning, so route it to
# the agent instead of the deterministic name/type/overview listers.
_GAP_RX = re.compile(
    r"\b(missing|do(n'?t| not) have|what.*(am i|i'?m)\s+missing|don'?t i have|lack(ing)?|"
    r"absent|need.*(that|which).*(do(n'?t| not) have)|should i have|yet to|haven'?t (got|received))\b",
    re.I)


def _looks_like_followup(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    if len(t.split()) <= 3:  # terse asks ('list both', 'and the dates?') lean on context
        return True
    return bool(_FOLLOWUP_RX.search(t))


def _contextualize_query(question: str, prior: list | None) -> str:
    """Rewrite a FOLLOW-UP ('can you list both documents', 'what about the second one')
    into a STANDALONE query using the recent turns, so retrieval AND the history-blind
    deterministic handlers can resolve it (otherwise a contentless follow-up retrieves
    nothing → INSUFFICIENT_EVIDENCE before the history block is ever seen). Returns the
    original text unchanged when there's no history, it isn't a follow-up, or the rewrite
    fails/looks wrong."""
    if not prior or not _looks_like_followup(question):
        return question
    s = get_settings()
    if not is_enabled("documents_general_fallback_enabled", True):  # reuse the general-fallback model toggle
        return question
    from app.llm import gateway
    model = s.documents_general_fallback_model or s.strong_extract_model
    if "/" not in model:  # gateway routes by provider prefix
        model = f"dashscope/{model}"
    convo = "\n".join(
        f"{'User' if (m.get('role') == 'user') else 'Assistant'}: {(m.get('text') or '')[:400]}"
        for m in (prior or [])[-4:])
    sys = (
        "You rewrite a user's FOLLOW-UP question into ONE standalone question that makes "
        "sense on its own. Resolve pronouns and references ('both', 'them', 'the second "
        "one', 'those') using the conversation, and carry over any document TYPE, PERSON "
        "name, or ENTITY implied by the prior turns (e.g. a follow-up after 'you have 2 "
        "national IDs' becomes 'list my national ID documents'). Output ONLY the rewritten "
        "question — no preamble, no quotes. If it is already standalone, echo it verbatim.")
    msgs = [gateway.Message(role="system", content=sys),
            gateway.Message(role="user",
                            content=f"Conversation:\n{convo}\n\nFollow-up: {question}\n\nStandalone question:")]
    try:
        r = gateway.call(model=model, messages=msgs, temperature=0.0, max_tokens=80)
        out = (r.text or "").strip().strip('"').strip()
        # Guard against runaway / empty / degenerate rewrites — fall back to the original.
        if out and 2 <= len(out) <= 300 and "\n" not in out:
            if out.lower() != question.strip().lower():
                log.info("contextualized follow-up %r -> %r", question, out)
            return out
    except Exception as e:  # noqa: BLE001
        log.warning("contextualize failed: %s", e)
    return question


# ── M47 · Regex intent pre-filter ──────────────────────────────────────────────
# Catches obvious deterministic queries before the LLM intent call. Only fires for
# first-questions (no prior history — follow-ups with pronouns need the LLM context
# resolver). Returns a _resolve_query-shaped dict or None if regex can't match.

# Pattern groups:
_COUNT_RX = re.compile(
    r"^how\s+many\s+(?P<type>[\w\s/-]+?)\s*(?:do\s+i\s+have|are\s+there|in\s+total)\s*\??$",
    re.IGNORECASE,
)
_LIST_RX = re.compile(
    r"^(?:list|show|display|what\s+are)\s+(?:all\s+)?(?:my\s+)?(?P<type>[\w\s/-]+?)\s*\??$",
    re.IGNORECASE,
)
_IDENTITY_RX = re.compile(
    r"^(?:what(?:'s|\s+is)\s+(?:my|the)\s+)?(?P<field>full\s+name|date\s+of\s+birth|dob|nationality|"
    r"nric|passport\s+number|address|email|phone|gender|sex)\s*\??$",
    re.IGNORECASE,
)
_MONEY_RX = re.compile(
    r"^(?:what(?:'s|\s+is)\s+(?:the\s+)?|show\s+(?:the\s+)?|calculate\s+(?:the\s+)?|get\s+(?:the\s+)?)"
    r"(?:total|sum|average|combined)\s+(?P<type>[\w\s/-]+?)\s*\??$",
    re.IGNORECASE,
)
_BARE_TOTAL_RX = re.compile(
    r"^(?:total|sum|average|combined)\s+(?P<type>[\w\s/-]+?)\s*\??$",
    re.IGNORECASE,
)
_WATCHLIST_RX = re.compile(
    r"^(?:what|which)\s+(?:is\s+)?(?:expiring|due|upcoming|renewal|expir)\w*\s*(?:soon|next|upcoming)?\s*\??$",
    re.IGNORECASE,
)
_CAPABILITY_RX = re.compile(
    r"^(?:what\s+(?:can|do)\s+you\s+(?:do|answer|help\s+with)|help\s*$)\s*\??$",
    re.IGNORECASE,
)
# Skip pre-filter for follow-ups: these need the LLM to resolve pronouns/context
_FOLLOWUP_RX = re.compile(
    r"\b(?:this|that|these|those|it|they|them|the\s+(?:first|second|last|above|previous|same|other|"
    r"former|latter|one|ones|following))\b",
    re.IGNORECASE,
)
_HAVE_RX = re.compile(
    r"^do\s+(?:i|we)\s+have\s+(?:a|an|any|the)\s+(?P<type>[\w\s/-]+?)\s*\??$",
    re.IGNORECASE,
)

# Map common type words to canonical doc_type values that the deterministic handlers expect
_TYPE_HINTS: dict[str, str] = {
    "invoice": "invoice", "invoices": "invoice",
    "receipt": "receipt", "receipts": "receipt",
    "bank statement": "bank_statement", "bank statements": "bank_statement",
    "credit card": "credit_card_statement", "credit card statement": "credit_card_statement",
    "credit card statements": "credit_card_statement",
    "passport": "passport", "passports": "passport",
    "national id": "national_id", "nric": "national_id",
    "certificate": "training_certificate", "certificates": "training_certificate",
    "training certificate": "training_certificate",
    "lab report": "lab_report", "lab reports": "lab_report",
    "medical report": "medical_report", "medical reports": "medical_report",
    "financial report": "financial_report", "financial reports": "financial_report",
    "academic transcript": "academic_transcript",
    "marketing material": "marketing_material",
    "reminder": "reminder_note", "reminder note": "reminder_note",
    "document": None, "documents": None, "file": None, "files": None,
}


def _regex_intent(
    question: str, prior: list | None, available_types: list[str] | None
) -> dict | None:
    """Try to determine intent from regex alone. Returns None if not confident."""
    q = (question or "").strip()
    if not q or len(q) < 3:
        return None
    # Follow-up questions need the LLM context resolver
    if _FOLLOWUP_RX.search(q):
        return None
    # Don't pre-filter if there's prior conversation (context-dependent)
    if prior and len(prior) >= 2:
        return None

    ql = q.lower().rstrip("?").strip()

    # ── Count queries: "how many invoices do I have?"
    m = _COUNT_RX.match(ql)
    if m:
        t = _TYPE_HINTS.get((m.group("type") or "").strip().lower())
        if t:
            return {"question": q, "names": [], "want": "count", "doc_types": [t], "label": None, "bm25_terms": ""}
        return {"question": q, "names": [], "want": "count", "doc_types": [], "label": None, "bm25_terms": ""}

    # ── List queries: "list all invoices"
    m = _LIST_RX.match(ql)
    if m:
        t = _TYPE_HINTS.get((m.group("type") or "").strip().lower())
        if t:
            return {"question": q, "names": [], "want": "list", "doc_types": [t], "label": None, "bm25_terms": ""}
        return None  # unknown type → let LLM handle

    # ── Have/existence: "do I have a passport?"
    m = _HAVE_RX.match(ql)
    if m:
        t = _TYPE_HINTS.get((m.group("type") or "").strip().lower())
        if t:
            return {"question": q, "names": [], "want": "count", "doc_types": [t], "label": None, "bm25_terms": ""}
        return None

    # ── Identity queries: "what is my NRIC?"
    m = _IDENTITY_RX.match(ql)
    if m:
        return {"question": q, "names": [], "want": "other", "doc_types": [], "label": None, "bm25_terms": ""}

    # ── Money queries: "total expenses" / "what is the total expenses"
    m = _MONEY_RX.match(ql) or _BARE_TOTAL_RX.match(ql)
    if m:
        t = _TYPE_HINTS.get((m.group("type") or "").strip().lower())
        dt = [t] if t else []
        return {"question": q, "names": [], "want": "other", "doc_types": dt, "label": None, "bm25_terms": ""}

    # ── Watchlist: "what expires soon?"
    if _WATCHLIST_RX.match(ql):
        return {"question": q, "names": [], "want": "other", "doc_types": [], "label": None, "bm25_terms": ""}

    # ── Capability: "what can you do?"
    if _CAPABILITY_RX.match(ql):
        return {"question": q, "names": [], "want": "other", "doc_types": [], "label": None, "bm25_terms": ""}

    return None


def _resolve_query(question: str, prior: list | None, available_types: list | None = None) -> dict:
    """LLM INTENT layer — the single 'understand what the user is asking' step in front of the
    deterministic handlers. Rewrites follow-ups to a standalone question (resolving 'this'/'these'/
    'both'/pronouns against the conversation) AND extracts clean entity-name filters + intent + the
    document TYPES a category/theme maps to (grounded in `available_types` = the user's real types),
    so routing no longer relies on brittle regex keyword-matching. Returns:
        {question, names: list[str], want: 'count'|'list'|'other', doc_types: list[str], label: str|None}
    Degrades to a no-op on any failure — the deterministic + RAG path still runs, so it can never be
    worse than before."""
    # M47 · regex pre-filter: catch obvious deterministic queries before the LLM intent call.
    # Saves ~30-40% of intent LLM calls for simple count/list/identity/money/watchlist patterns.
    _re = _regex_intent(question, prior, available_types)
    if _re:
        return _re

    base = {"question": question, "names": [], "want": "other", "doc_types": [], "label": None,
            "bm25_terms": ""}
    s = get_settings()
    if not is_enabled("documents_general_fallback_enabled", True):
        return base
    from app.llm import gateway
    model = s.documents_general_fallback_model or s.strong_extract_model
    if "/" not in model:
        model = f"dashscope/{model}"
    convo = "\n".join(
        f"{'User' if (m.get('role') == 'user') else 'Assistant'}: {(m.get('text') or '')[:400]}"
        for m in (prior or [])[-4:])
    types_line = (", ".join(sorted(available_types)) if available_types else "(unknown)")
    sys = (
        "You parse a question to a PERSONAL-DOCUMENTS assistant into structured intent. The user "
        "has a library of their own documents. Return ONLY a JSON object with these keys:\n"
        '  "question": the new question rewritten to STAND ALONE — resolve pronouns and references '
        "(\"this\", \"these\", \"both\", \"them\", \"the second one\") using the CONVERSATION.\n"
        '  "names": array of PERSON or ORGANISATION names the user is filtering documents by — clean '
        "names only (e.g. [\"Kalyani\"]); [] if not filtering by a name. For a follow-up that NARROWS "
        "a previous result (e.g. after listing documents about Rajesh, 'out of these how many have "
        "Kalyani'), include BOTH names — the NEW one first: [\"Kalyani\", \"Rajesh\"].\n"
        '  "doc_types": array of document types the question targets, chosen ONLY from this list of '
        f"the user's ACTUAL types: [{types_line}]. For a specific type ('invoices' → [\"invoice\"]); "
        "for a CATEGORY or THEME pick ALL that fit — e.g. EXPENSE/spending/financial → invoice, "
        "receipt, bank_statement, credit_card_statement, customer_payment, financial_report; "
        "IDENTITY/ID → national_id, passport, drivers_license. [] if the question isn't about a type "
        "or category.\n"
        '  "label": a short noun for the category if doc_types came from a theme (e.g. "expense-related"), '
        "else null.\n"
        '  "want": "count" if they just ask HOW MANY; "list" if they just want to LIST/show WHICH '
        "documents (names only); \"other\" for anything richer — a question that also wants per-document "
        "DETAILS (dates, amounts, expiry, specific fields), a comparison, a summary, or any reasoning "
        "(e.g. 'list my certificates AND when they expire' → \"other\"). When unsure, prefer \"other\".\n"
        '  "bm25_terms": a space-separated string of English search keywords for full-text matching. '
        "Extract the KEY content words (nouns, numbers, identifiers, document names). If the question is "
        "NOT in English, generate English equivalents of the key terms. Remove function words (the, a, is, "
        "do, have, my). For example: 'show me my electricity bills from last month' → 'electricity bill "
        "last month'; 'combien de factures' → 'invoices count'; '私のパスポート番号' → 'passport number'. "
        "Keep it to 6 words max. Empty string if the question is pure meta (greetings, 'help').\n"
        "Only choose doc_types from the provided list. Output JSON only, no prose, no code fences.")
    msgs = [gateway.Message(role="system", content=sys),
            gateway.Message(role="user", content=f"Conversation:\n{convo}\n\nNew question: {question}\n\nJSON:")]
    try:
        r = gateway.call(model=model, messages=msgs, temperature=0.0, max_tokens=260)
        txt = (r.text or "").strip()
        txt = re.sub(r"^```(?:json)?|```$", "", txt, flags=re.I | re.M).strip()
        import json
        try:
            data = json.loads(txt)
        except Exception:  # noqa: BLE001
            from json_repair import repair_json
            data = json.loads(repair_json(txt))
        if not isinstance(data, dict):
            return base
        q = (str(data.get("question") or "").strip() or question)
        names = [str(n).strip() for n in (data.get("names") or []) if str(n).strip()][:4]
        want = data.get("want") if data.get("want") in ("count", "list", "other") else "other"
        # Keep only types the user actually has (the model was told to, but enforce it).
        avail = {t.lower() for t in (available_types or [])}
        dts = [str(t).strip() for t in (data.get("doc_types") or []) if str(t).strip()]
        dts = [t for t in dts if not avail or t.lower() in avail][:20]
        label = data.get("label")
        label = str(label).strip() if label else None
        bm25_terms = str(data.get("bm25_terms") or "").strip()[:200]
        out = {"question": q, "names": names, "want": want, "doc_types": dts, "label": label,
               "bm25_terms": bm25_terms}
        if names or dts or q.lower() != (question or "").strip().lower():
            log.info("resolve_query %r -> %s", question, out)
        return out
    except Exception as e:  # noqa: BLE001
        log.warning("resolve_query failed: %s", e)
        return base


def post_message(
    db: Session, tenant_id: str, vendor_pk: int | None, user_text: str,
    doc_ids: list[str] | None = None, group_id: int | None = None,
    conv_id: str | None = None,
) -> dict:
    """Persist the user question, run cross-doc RAG + one LLM call, persist
    and return the AI answer with doc-attributed citations.

    `doc_ids` (subset mode) restricts retrieval to the ticked documents; None
    or empty means the full vendor workspace. `conv_id` selects which saved
    conversation this message belongs to (None = the base thread).
    """
    from app import retrieval

    wkey = workspace_key(vendor_pk, group_id, conv_id)
    user_msg = ChatMessage(
        tenant_id=tenant_id,
        requirement_id_external=None,
        doc_id_external=None,
        workspace_key=wkey,
        role="user",
        text=user_text,
    )
    db.add(user_msg)
    db.flush()

    settings = get_settings()
    _docs_mode = settings.product == "documents"

    # M46 · INPUT guardrail · deterministic prompt-injection / jailbreak screen.
    if _docs_mode and settings.documents_chat_guardrail:
        from app.agents import chat_guardrail
        refusal = chat_guardrail.guard_input(user_text)
        if refusal:
            return _persist_ai(db, tenant_id, wkey, text=refusal, citations=[],
                               meta="workspace · guard_input")

    # Destructive/mutating asks ("delete all my documents") → a helpful pointer, not a canned tally.
    if _docs_mode and not doc_ids:
        _act = _unsupported_action(user_text)
        if _act:
            return _persist_ai(db, tenant_id, wkey, text=_act, citations=[],
                               meta="workspace · unsupported_action")

    # INTENT layer · one LLM call resolves the question to a standalone form (follow-ups,
    # pronouns, "out of these …") AND extracts clean entity-name filters + intent — so routing
    # no longer depends on brittle regex keyword-matching. `q` (the resolved question) drives
    # every downstream handler + retrieval; the persisted user bubble keeps the raw text.
    q = user_text
    _intent = {"question": user_text, "names": [], "want": "other", "doc_types": [], "label": None}
    if _docs_mode:
        _types = owner_doc_types(db, tenant_id) if (not doc_ids and group_id is None) else None
        _intent = _resolve_query(user_text, _history(db, tenant_id, wkey, exclude_pk=user_msg.pk), _types)
        q = _intent["question"] or user_text
    _dt0 = _intent["doc_types"][0] if len(_intent["doc_types"]) == 1 else None
    # A "what am I missing / don't have" question must reason about ABSENCE, not list what's
    # present — send it to the agent by suppressing the deterministic listers below.
    _gap = bool(_GAP_RX.search(q))

    # Name-filter intent (person/org) — answered from the entity graph + extracted fields with
    # CLEAN names from the intent layer, incl. INTERSECTION for narrowing follow-ups ('of the
    # Rajesh docs, how many also have Kalyani'). Only for explicit list/count-by-name asks so a
    # 'summarise X's invoice' still goes to RAG.
    if (_docs_mode and not doc_ids and group_id is None
            and _intent["names"] and _intent["want"] in ("count", "list") and not _gap):
        _nm = answer_name_query(db, tenant_id, _intent["names"], _intent["want"], _dt0)
        if _nm:
            return _persist_ai(db, tenant_id, wkey, text=_nm, citations=[],
                               meta="workspace · name_query")

    # Type / category listing — "list all invoices", "list all expense related documents". The
    # intent layer resolved a category ('expense related') into the user's ACTUAL doc types, so
    # this lists exactly those. Only when no name filter is in play.
    if (_docs_mode and not doc_ids and group_id is None
            and not _intent["names"] and _intent["doc_types"] and _intent["want"] in ("count", "list")
            and not _gap):
        _tl = answer_doc_type_query(db, tenant_id, _intent["doc_types"], _intent["want"], _intent["label"])
        if _tl:
            return _persist_ai(db, tenant_id, wkey, text=_tl, citations=[],
                               meta="workspace · type_query")

    # Deterministic aggregations the agent handles unreliably (type-specific counts, oldest/newest,
    # money totals, comparisons, self-profile) — answered from SQL/data, before the greedy overview +
    # the flaky agent loop. Fast + correct.
    # LEAN-TO-AGENT: when the intent layer says the question is rich ("other" — wants specific fields,
    # per-doc details, reasoning), skip these greedy handlers entirely so the agent (which can look up
    # the exact field / chain tools) answers it, rather than a narrow handler grabbing it first.
    # P2 · cloud-only lean-to-agent gate — OSS stays deterministic.
    _agent_fb = is_enabled("documents_agent_fallback", True)
    if (_docs_mode and not doc_ids and group_id is None
            and not (is_cloud() and _agent_fb and _intent["want"] == "other")):
        # skip_name_regex: the intent layer already owns name filtering (above) — don't let the
        # regex name handlers grab garbage out of fact questions ('what is Kalyani's date of birth').
        _det = deterministic_answer(db, tenant_id, q, skip_name_regex=True)
        if _det:
            return _persist_ai(db, tenant_id, wkey, text=_det, citations=[],
                               meta="workspace · deterministic_aggregate")

    # M46 · scale guard · a whole-library "summarize everything / what do I have"
    # can't be a content summary across thousands of docs (you can't feed them
    # all to an LLM). Answer with a deterministic aggregate — SQL-only, no row
    # loading, scales to ANY N. Only when the user didn't tick a subset.
    if (_docs_mode and not doc_ids and group_id is None and _is_broad_overview(q) and not _gap
            and not (is_cloud() and _agent_fb and _intent["want"] == "other")):
        return _persist_ai(
            db, tenant_id, wkey, text=_aggregate_overview(db, tenant_id),
            citations=[], meta="workspace · aggregate_overview",
        )

    # HYBRID · the tool-using workspace agent handles everything the deterministic fast-paths above
    # didn't. It ALWAYS runs for ACTION / STRUCTURED requests (group / rename / tag / export / table /
    # compare / duplicates); with `documents_agent_fallback` on it ALSO handles the general long tail
    # (content, analysis, cross-doc reasoning) over the shared tool set — instead of straight-to-RAG.
    # Runs before the empty-scope guard so actions work with 0 docs. Agent errors fall back to RAG.
    # P2 · cloud-only — OSS deployments fall through to RAG.
    if (is_cloud() and _docs_mode and is_enabled("documents_agentic_chat", True) and not doc_ids and group_id is None
            and (is_enabled("documents_agent_fallback", True)
                 or _wants_agent(q, _history(db, tenant_id, wkey, exclude_pk=user_msg.pk)))):
        try:
            from app.agents import workspace_agent
            prior = _history(db, tenant_id, wkey, exclude_pk=user_msg.pk)
            result = workspace_agent.run(db, question=q, tenant_id=tenant_id, prior=prior)
            answer = result.get("answer") if isinstance(result, dict) else result
            if answer and answer.strip():
                # A bare 'not found in your documents' may be an OFF-TOPIC / general question
                # (greeting, weather, world fact) — let the general assistant try before dead-ending.
                low = answer.strip().lower()
                if ("not found in your documents" in low[:80] and len(low) < 140):
                    g = _general_assistant(q, prior)
                    if g:
                        return _persist_ai(db, tenant_id, wkey, text=g, citations=[],
                                           meta="workspace · general_assistant")
                return _persist_ai(db, tenant_id, wkey, text=answer,
                                   citations=(result.get("citations") if isinstance(result, dict) else None) or [],
                                   meta="workspace · agent",
                                   trace=(result.get("steps") if isinstance(result, dict) else None),
                                   artifacts=(result.get("artifacts") if isinstance(result, dict) else None))
        except Exception as e:  # noqa: BLE001
            log.warning("workspace agent failed, falling back to RAG: %s", e)

    # Cap the cross-doc retrieval scope so a huge library doesn't load every row
    # + scan every chunk; content questions retrieve over the most-recent N.
    cap = settings.documents_workspace_max_docs if _docs_mode else None
    docs = resolve_scope_docs(db, tenant_id, vendor_pk, doc_ids=doc_ids, limit=cap, group_id=group_id)
    if not docs:
        return _persist_ai(
            db, tenant_id, wkey,
            text="No documents are ready in this workspace yet. Upload and "
                 "let a document finish processing, then ask again.",
            citations=[], meta="workspace · empty_scope",
        )

    # M46 · "list/share all <type>" → answer by CLASSIFICATION (correct recall +
    # type filter), not content RAG which misses the real doc and pulls docs that
    # merely mention an identifier.
    if _docs_mode and not doc_ids and group_id is None:
        list_phrase = _detect_list_request(q)
        if list_phrase:
            return _answer_type_listing(
                db, tenant_id, wkey, list_phrase, _matched_docs_by_type(docs, list_phrase),
            )

    doc_pks = [d.pk for d in docs]
    name_by_id = {d.id_external: d.name for d in docs}
    # M46 · the document's CLASSIFIED type, so the model can tell "this doc IS a
    # national ID" from "this insurance certificate merely mentions an NRIC".
    type_by_name = {d.name: (d.doc_type or "unclassified") for d in docs}

    # R5 · query routing + multi-hop decomposition + corrective-RAG (opt-in).
    # Routes the question (no-retrieval / single-hop / multi-hop), decomposes
    # cross-document questions into sub-queries, and re-retrieves on weak
    # evidence — returning the same Hit objects so everything below is unchanged.
    _r5_meta = None
    if settings.chat_query_routing:
        from app.services import query_router as _qr
        hits, _r5_meta = _qr.route_and_retrieve(
            db, q, doc_pks=doc_pks, top_k=_TOP_K,
            min_hits=settings.chat_abstain_min_hits,
            min_top_score=settings.chat_abstain_min_top_score,
            max_hops=settings.chat_crag_max_hops,
            max_subqs=settings.chat_multihop_max_subqs,
        )
    else:
        hits = retrieval.retrieve(db, q, top_k=_TOP_K, doc_pks=doc_pks,
                                 bm25_terms=_intent.get("bm25_terms", ""))
    # R1 · calibrated abstention — refuse cleanly when evidence is too weak
    # rather than answer from nothing (better than a confident hallucination).
    from app import abstention
    if settings.chat_abstain_enabled:
        abstain, _why = abstention.assess_evidence(
            [getattr(h, "score", None) for h in hits],
            min_hits=settings.chat_abstain_min_hits,
            min_top_score=settings.chat_abstain_min_top_score,
        )
    else:
        abstain = not hits
    if abstain:
        # No document evidence. If it's an OFF-TOPIC / general question (weather, world facts, coding
        # help), the general assistant answers it; if it's really about their docs it self-gates
        # (NEED_DOCUMENT) and we keep the honest doc dead-end.
        g = _general_assistant(q, _history(db, tenant_id, wkey, exclude_pk=user_msg.pk))
        if g:
            return _persist_ai(db, tenant_id, wkey, text=g, citations=[],
                               meta="workspace · general_assistant")
        return _persist_ai(
            db, tenant_id, wkey,
            text=abstention.refusal_message(n_docs=len(docs)),
            citations=[], meta="workspace · insufficient_evidence",
        )

    # Compose evidence excerpts labeled with their SOURCE DOCUMENT so the
    # model can attribute facts across docs (and so can the citations).
    evidence = hits[:_MAX_EVIDENCE]
    from app.services.chat_pipeline import format_evidence_block
    evidence_block = format_evidence_block(
        evidence, cap=500, show_name=True, type_by_name=type_by_name,
        empty="(no evidence retrieved)")

    doc_list = "\n".join(f"  · {d.name}" for d in docs[:40])
    if len(docs) > 40:
        doc_list += f"\n  · …and {len(docs) - 40} more"

    _intro = (
        "You are DocAIQuest — a document audit assistant answering across a SET "
        "of documents. Be precise. No filler.\n\n"
    )
    _applied_frags: list[str] | None = None
    if settings.answer_fragments_enabled:
        # #3 · assemble RULES from BASE + only the fragments this question needs.
        # Included set is always a SUBSET of the legacy block below (same-or-cleaner).
        from app.services.answer_fragments import build_rules_block
        _rules_block, _applied_frags = build_rules_block(q)
        system = _intro + _rules_block
    else:
        system = (
            _intro +
            "RULES:\n"
            "  · Use the STRUCTURED FIELDS and evidence excerpts below. If neither "
            "contains the answer, reply: 'Not found in the retrieved evidence.'\n"
            "  · For attribute/value questions (who is the applicant, the total, a "
            "date), PREFER the STRUCTURED FIELDS — they carry ROLE labels, so use "
            "them to pick the RIGHT value (e.g. the Applicant name, NOT an "
            "emergency-contact or other name that also appears in the text). Use "
            "evidence excerpts to confirm or supplement.\n"
            "  · ALWAYS name the source document when you state a fact "
            "(e.g. 'Per Insurance_Cert.pdf, …'). Facts may come from different "
            "documents — keep them attributed.\n"
            "  · Comparison / 'across all' questions → a short markdown table, "
            "one row per document.\n"
            "  · Single-value question → one line with the value + its source doc.\n"
            "  · Each excerpt is tagged with its source document's TYPE "
            "(type=…). When the question asks for documents OF a kind (e.g. "
            "'national IDs', 'invoices', 'insurance policies'), include ONLY "
            "documents whose TYPE matches the kind asked for. A document that merely "
            "MENTIONS an identifier is NOT that kind — e.g. an insurance certificate "
            "(type=motor_insurance_certificate) that lists the holder's NRIC is NOT "
            "a national ID; do not list it as one.\n"
            "  · Never invent. Never explain what you're doing."
        )
    history_block = _format_history_block(_history(db, tenant_id, wkey, user_msg.pk))
    fields_block = _structured_fields_block(docs, evidence)
    user_block = (
        f"{history_block}"
        f"Documents in this workspace ({len(docs)}):\n{doc_list}\n\n"
        f"{fields_block}"
        f"Evidence excerpts (retrieved across the documents via hybrid "
        f"BM25 + cosine + reranker):\n\n{evidence_block}\n\n"
        f"Current question: {q}"
    )

    pii_terms = _pii_extra_terms(docs)
    answer = ""
    # #4 · HYBRID typed answer contract (flag-gated). Table/comparison answers stay
    # free-text — a markdown table inside a JSON string field is unreliable (eval showed
    # a 95→85% format regression) — while everything else uses the typed {answer,
    # answer_found, format, caveats} object for the explicit answer_found / clean-abstain
    # gate. Any failure falls through to the free-text call below, so it can't be worse.
    if settings.typed_answer_enabled:
        from app.services.answer_fragments import expected_format as _xf
        if _xf(q) != "table":
            try:
                from app.services import typed_answer as _ta
                ta = _ta.generate(db, system, user_block, max_tokens=700, extra_terms=pii_terms)
                if ta is not None:
                    answer = ta.rendered()
            except Exception as e:  # noqa: BLE001
                log.warning("workspace chat typed-answer failed: %s", e)
    if not answer:
        try:
            answer = doc_chat_service.llm_one_shot(
                db, system, user_block, max_tokens=700, cache_system=True,
                extra_terms=pii_terms,
            ).strip()
        except Exception as e:  # noqa: BLE001
            log.warning("workspace chat LLM failed: %s", e)
            answer = ""
    if not answer:
        answer = (
            "The assistant is temporarily unavailable (LLM provider error or "
            "rate limit). Please try again in a moment."
        )
        meta = "workspace · llm_unavailable"
    else:
        meta = "workspace · rag"
        if _r5_meta:  # R5 · surface the route (single_hop / multi_hop / no_retrieval)
            meta += f" · {_r5_meta.get('route')}"
        # M46 · OUTPUT guardrail · critique the answer for grounding + correct
        # document TYPE. On a flag, regenerate once with the issue, then caveat.
        if _docs_mode and settings.documents_chat_guardrail:
            from app.agents import chat_guardrail
            grounded, issue = chat_guardrail.critique(db, q, evidence_block, answer, extra_terms=pii_terms)
            if not grounded:
                fix_block = (
                    f"{user_block}\n\nA REVIEWER FLAGGED YOUR DRAFT: {issue}\n"
                    "Re-answer using ONLY the evidence. Include ONLY documents whose "
                    "type matches what was asked. Drop anything unsupported."
                )
                try:
                    revised = (doc_chat_service.llm_one_shot(db, system, fix_block, max_tokens=700, extra_terms=pii_terms) or "").strip()
                except Exception:  # noqa: BLE001
                    revised = ""
                if revised:
                    grounded2, _ = chat_guardrail.critique(db, q, evidence_block, revised, extra_terms=pii_terms)
                    answer = revised
                    meta = "workspace · rag+guarded" if grounded2 else "workspace · rag+flagged"
                    still_ungrounded = not grounded2
                else:
                    still_ungrounded = True
                    meta = "workspace · rag+flagged"
                if still_ungrounded:
                    # R1 · strict mode → refuse; otherwise the softer caveat.
                    if abstention.abstain_after_guardrail(False, hard=settings.chat_abstain_on_ungrounded):
                        answer = abstention.refusal_message(n_docs=len(docs))
                        meta = "workspace · insufficient_evidence"
                    else:
                        answer += "\n\n_⚠ This answer may not be fully grounded in your documents — please verify against the source._"

    # R2 · chain-of-verification — per-claim semantic faithfulness (opt-in; +1 LLM call).
    if (settings.chat_claim_verification and meta.startswith("workspace · rag")
            and not abstention.is_abstention(answer)):
        from app.agents import claim_verifier as cv
        verified = cv.verify(db, cv.split_claims(answer), evidence_block, extra_terms=pii_terms)
        vsum = cv.summarize(verified)
        if not vsum["all_supported"]:
            if settings.chat_claim_drop_unsupported:
                kept = cv.drop_unsupported(verified)
                if kept:
                    answer = kept
                meta = "workspace · rag+claim_pruned"
            else:
                flags = "; ".join(f["claim"][:60] for f in vsum["flags"][:3])
                answer += f"\n\n_⚠ {vsum['unsupported']} claim(s) not verified against the source: {flags}_"
                meta = "workspace · rag+claim_flagged"

    # Evidence-level citations (one per retrieved passage) — the baseline + fallback.
    evidence_citations = [
        {
            "chunkPk": int(h.chunk_pk),
            "page": int(h.page),
            "docId": h.document_id_external,
            "docName": name_by_id.get(h.document_id_external, h.document_name),
            "bbox": None,
            "quote": (h.text or "")[:200],
            "text": h.text or "",  # used by R3 attribution; stripped from output
        }
        for h in evidence[:_MAX_CITATIONS]
    ]
    citations = [{k: v for k, v in c.items() if k != "text"} for c in evidence_citations]

    # R3 · per-sentence citations + drop-invalid. Attribute each answer sentence
    # to its supporting passage; cite only what's actually used. Skip for
    # abstentions / errors. Falls back to evidence-level if nothing attributes
    # (never show fewer citations than before).
    if (settings.chat_sentence_citations and meta.startswith("workspace · rag")
            and not abstention.is_abstention(answer)):
        from app import sentence_citations as sc
        attrs = sc.attribute(sc.split_sentences(answer), evidence_citations,
                             min_support=settings.chat_sentence_support_min)
        sent_cites = sc.citations_from_attributions(attrs)
        if sent_cites:
            citations = sent_cites
        if settings.chat_drop_unsupported_sentences:
            stripped = sc.supported_answer(attrs)
            if stripped:
                answer = stripped

    # A grounded "not found" refusal (distinct from the abstain sentinel) is a
    # NON-answer: R3 attribution finds nothing to support, so `citations` was left as
    # the full evidence list — attaching source chips to "I couldn't find it" is
    # misleading. Drop the chips, and since it may actually be an OFF-TOPIC / general
    # question, try the general assistant before returning the dead-end (this is the
    # fallback that the old `if not citations` guard could never reach, because
    # citations was always non-empty here). Detect only when the answer LEADS with the
    # refusal (first ~90 chars), so a valid answer that merely mentions "not found"
    # mid-sentence is never discarded.
    _head = (answer or "").strip().lower()[:90]
    model_refused = ("not found in the retrieved evidence" in _head
                     or "insufficient_evidence" in _head)
    if model_refused:
        citations = []
        g = _general_assistant(q, _history(db, tenant_id, wkey, exclude_pk=user_msg.pk))
        if g:
            return _persist_ai(db, tenant_id, wkey, text=g, citations=[],
                               meta="workspace · general_assistant")

    return _persist_ai(db, tenant_id, wkey, text=answer, citations=citations, meta=meta)


def _persist_ai(
    db: Session, tenant_id: str, wkey: str, *, text: str, citations: list, meta: str,
    trace: list | None = None, artifacts: list | None = None,
) -> dict:
    msg = ChatMessage(
        tenant_id=tenant_id,
        requirement_id_external=None,
        doc_id_external=None,
        workspace_key=wkey,
        role="ai",
        text=text,
        citations=citations,
        meta=meta,
        trace=trace or None,
        tools=artifacts or None,
    )
    db.add(msg)
    db.commit()
    return _msg_to_dict(msg)
