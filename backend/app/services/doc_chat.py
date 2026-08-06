"""Doc-scoped chat helpers — extracted from `routers/doc_chat.py` (TODO #25).

These are pure functions: take db + plain args, return data. No HTTP
concerns, no request/response shaping. The router stays in charge of
auth / status codes / pagination; the service owns the prompt-building
+ LLM dispatch + citation scoring.

This is a conservative extraction — the route handlers in the router
still own their orchestration. As the routers shrink we can pull more
logic in here (full `post_message` flow, etc.) without changing the
service surface.
"""
from __future__ import annotations

import json
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.llm import gateway
from app.llm.router import route
from app.orm import Document, DocumentChunk
from app.repositories import routing_configs as rc_repo

log = logging.getLogger(__name__)


# Sentinel the facts-first LLM emits when the structured facts don't cover
# the question. The caller treats this as the signal to fall through to
# retrieval. Distinctive enough that the model won't accidentally produce it.
FACTS_NOT_FOUND_SENTINEL = "FACTS_NOT_APPLICABLE"


# M31.4 · Identity guard helpers. The facts-first chat path was leaking
# wrong-person answers: user asks "what is the DOB of Rajesh Goda?" on
# Kalyani's passport (extracted as first_name=KALYANI middle_name=GODA
# last_name=RAJESH), and the LLM returned Kalyani's DOB attributed to
# Rajesh — because "RAJESH" + "GODA" both appear in the facts blob. The
# LLM did fuzzy matching the prompt didn't forbid strongly enough.
#
# Defense in depth: programmatic name match BEFORE the LLM call.

import re as _re  # noqa: E402 — deliberately near the identity-guard helpers below


def _holder_name_from_fields(fields: dict) -> str | None:
    """Best-effort doc holder/subject name from extracted_fields.fields.
    Tries flat fields first (name/full_name/holder_name/etc.), falls back
    to first+middle+last composition. Returns None when nothing found."""
    if not isinstance(fields, dict):
        return None
    for key in ("holder_name", "full_name", "name", "applicant_name",
                "subject_name", "account_holder"):
        v = fields.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    parts = []
    for key in ("first_name", "middle_name", "last_name", "surname"):
        v = fields.get(key)
        if isinstance(v, str) and v.strip():
            parts.append(v.strip())
    return " ".join(parts) if parts else None


# Common English words that look like proper nouns when capitalized at
# sentence start. Filter from candidate person names so 'Is Kalyani' →
# 'Kalyani' instead of 'Is Kalyani'.
_NON_NAME_LEADERS = frozenset({
    "is", "are", "was", "were", "do", "does", "did", "has", "have", "had",
    "can", "could", "should", "would", "will", "may", "might", "must",
    "who", "what", "where", "when", "why", "how", "which",
    "this", "that", "these", "those", "the", "a", "an",
    "for", "of", "on", "in", "at", "to", "by", "with",
    "show", "tell", "give", "find", "explain", "verify", "check",
})

# Trailing tokens to drop · acronyms / field labels that look like names
# when capitalized but aren't ('DOB', 'ID', 'KYC', 'SSN', 'PAN', 'NRIC').
_NON_NAME_TRAILERS = frozenset({
    "dob", "id", "name", "address", "passport", "kyc", "ssn", "pan",
    "nric", "aadhar", "aadhaar", "ein", "tin", "vat", "iban",
    "details", "data", "info", "information", "record", "records",
})


def _strip_non_name_leaders(name: str) -> str:
    """Drop leading words that are auxiliary verbs / interrogatives /
    articles. Also drop trailing acronyms / field labels. So:
    'Is Kalyani Rajesh's DOB' → 'Kalyani Rajesh'."""
    toks = name.split()
    while toks and toks[0].lower() in _NON_NAME_LEADERS:
        toks = toks[1:]
    while toks and toks[-1].lower().rstrip("'s") in _NON_NAME_TRAILERS:
        toks = toks[:-1]
    return " ".join(toks)


def _extract_person_from_question(question: str) -> str | None:
    """Pull a candidate person name from the user's question. Looks for
    common framings ('DOB of <Name>', 'about <Name>', etc.) + bare 2+
    capitalized-word sequences. Returns None when no likely person
    mention is present. Strips leading auxiliary words ('Is', 'Does', etc.)
    so 'Is Kalyani Rajesh's DOB correct?' yields 'Kalyani Rajesh' not
    'Is Kalyani Rajesh's DOB'."""
    if not question:
        return None
    # Common framings — preposition-anchored. 's at end is allowed
    # ('about Rajesh's history') and stripped after capture.
    for pat in (
        r"\bof\s+([A-Z][a-zA-Z']+(?:\s+[A-Z][a-zA-Z']+)+)",
        r"\babout\s+([A-Z][a-zA-Z']+(?:\s+[A-Z][a-zA-Z']+)+)",
        r"\bfor\s+([A-Z][a-zA-Z']+(?:\s+[A-Z][a-zA-Z']+)+)",
    ):
        m = _re.search(pat, question)
        if m:
            cand = m.group(1).rstrip("'s").rstrip("'").strip()
            cand = _strip_non_name_leaders(cand)
            if cand:
                return cand
    # Fallback · 2+ capitalized words. Strip leading auxiliary verbs.
    m = _re.search(r"([A-Z][a-zA-Z']{1,}(?:\s+[A-Z][a-zA-Z']{1,}){1,})", question)
    if m:
        cand = m.group(1).rstrip("'s").rstrip("'").strip()
        cand = _strip_non_name_leaders(cand)
        # After stripping leaders, we need at least one token left.
        if cand and len(cand.split()) >= 1:
            return cand
    return None


def _names_match(question_name: str, doc_holder: str) -> bool:
    """Strict name match for KYC identity guard.

    We prefer FALSE NEGATIVES (refuse with 'this doc is for X, not Y')
    over FALSE POSITIVES (silently answer about the wrong person). KYC
    audits demand the conservative side.

    Match rules (any one is sufficient):
      1. Exact token-set equality after normalization.
      2. First-token equality AND question is a subset of holder tokens.
         (i.e. 'Rajesh Goda' matches 'Rajesh Kumar Goda' but not
         'Kalyani Goda Rajesh' — first token differs.)

    Specifically WON'T match: 'Rajesh Goda' vs 'Kalyani Goda Rajesh'
    even though both tokens appear in the holder, because the given
    name (first token) is different. That's the bug we're fixing."""
    def _toks(s: str) -> list[str]:
        # Drop 's possessives before stripping punctuation so tokens come
        # out clean: "kalyani rajesh's" → ["kalyani", "rajesh"].
        s = _re.sub(r"'s\b", "", (s or "").lower())
        s = _re.sub(r"[^a-z0-9 ]+", "", s).strip()
        return s.split()
    q = _toks(question_name)
    d = _toks(doc_holder)
    if not q or not d:
        return False
    # Rule 1 · exact set equality
    if set(q) == set(d):
        return True
    # Rule 2 · first-token equality + q ⊆ d (or d ⊆ q)
    if q[0] == d[0] and (set(q).issubset(set(d)) or set(d).issubset(set(q))):
        return True
    return False


def check_identity_guard(question: str, fields: dict) -> str | None:
    """If the question references a specific person who does NOT match
    the document's holder/subject, return a refusal answer string. Else
    return None (caller proceeds with normal answer)."""
    holder = _holder_name_from_fields(fields)
    if not holder:
        return None
    asked = _extract_person_from_question(question)
    if not asked:
        return None
    if _names_match(asked, holder):
        return None
    return (
        f"This document is for **{holder}**, not {asked}. "
        f"Information about {asked} is not in this document."
    )


def doc_text_excerpt(db: Session, document_pk: int, max_chars: int = 8000) -> str:
    """Concatenate the doc's chunks for a one-shot LLM call. Default 8K chars
    (~2K tokens) — small enough to stay fast on free-tier models and avoid
    rate-limit hangs, large enough for a meaningful summary / conversion."""
    rows = db.scalars(
        select(DocumentChunk.text)
        .where(DocumentChunk.document_pk == document_pk)
        .order_by(DocumentChunk.chunk_index)
    ).all()
    joined = "\n\n".join((r or "") for r in rows)
    if len(joined) > max_chars:
        joined = joined[:max_chars] + "\n\n[...truncated for one-shot conversion...]"
    return joined


def llm_one_shot(
    db: Session, system: str, user: str, *,
    max_tokens: int = 800,
    cache_system: bool = False,
    cache_prefix: str | None = None,
    extra_terms: "list[tuple[str, str]] | None" = None,
    model: str | None = None,
    structured: bool = False,
) -> str:
    """`cache_prefix` (M50) — a large STABLE block (e.g. the document body) sent
    as a separate cache_control:ephemeral user part so Anthropic caches it across
    turns (~90% input discount). It's still PII-redacted in the gateway. The
    varying `user` (question/history) goes in a second, uncached part."""
    """Single text-only LLM call. Bypasses the full cascade — uses the first
    available tier-1 model directly so we can:
      - control max_tokens per task (summary=600, markdown=2400, json=1500)
      - skip the multi-tier retry overhead for tasks that don't need it
      - return faster on free-tier providers (no failover loop on rate-limit)
    Falls back to the legacy cascade if no tier-1 model can be picked.

    `cache_system=True` flags the system block for Anthropic prompt
    caching (90% discount on the cached prefix). Silently ignored by
    backends that don't support it.
    """
    cfg = rc_repo.get(db) or {}
    tiers = cfg.get("tiers") or []
    t1 = next((t for t in tiers if t.get("id") == "t1"), tiers[0] if tiers else None)
    # #4 · BYO-model: an explicit `model` (e.g. from a partner API caller) overrides
    # the tenant's tier-1 selection. Routed via the gateway's prefix→provider map
    # using the PLATFORM's keys (caller-supplied keys = a separate follow-up).
    model_id = model
    if not model_id and t1:
        for m in t1.get("models") or []:
            if m and m.get("status", "active") == "active" and m.get("id"):
                model_id = m["id"]
                break
    # Build the user content. With a cache_prefix, send two parts: the stable
    # doc block (cacheable) then the varying question. Else a plain string.
    if cache_prefix:
        user_content: "str | list[dict]" = [
            {"type": "text", "text": cache_prefix, "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": user},
        ]
        fallback_user = cache_prefix + "\n\n" + user  # route() has no caching
    else:
        user_content = user
        fallback_user = user
    if not model_id:
        decision = route(
            db, task="validate",
            messages=[
                gateway.Message(role="system", content=system),
                gateway.Message(role="user", content=fallback_user),
            ],
        )
        return decision.text or ""
    # M36 · free-tier LLM rate limit. This direct gateway.call() bypasses
    # route(), where check_and_record_llm_call normally lives — so without
    # this gate, doc-chat / summary / export would let a free tenant burn the
    # shared container's LLM key past the 5-calls/hour cap. No-op for paid.
    # (The no-model branch above already gated via route().)
    from app.plan_limits import check_and_record_llm_call
    check_and_record_llm_call(db)
    # M44.P11 · pass the tenant through so gateway.call applies PII redaction
    # (when DOCAIQ_PII_REDACT_BEFORE_LLM is on) and writes the audit row.
    # Without this, every one-shot path (doc chat, summary, markdown/json
    # export, workspace cross-doc chat, rag_retrieval) would silently bypass
    # both. get_current_tenant() is set by TenantMiddleware on the request
    # path and explicitly in worker jobs.
    from app.db import get_current_tenant as _get_tid
    try:
        _tid = _get_tid()
    except Exception:  # noqa: BLE001
        _tid = None
    try:
        result = gateway.call(
            model_id,
            [gateway.Message(role="system", content=system),
             gateway.Message(role="user", content=user_content)],
            structured=structured,
            max_tokens=max_tokens,
            cache_system=cache_system,
            tenant_id=_tid,
            task_kind="doc_oneshot",
            extra_terms=extra_terms,
        )
        # structured callers want the (detokenized) JSON text to parse themselves.
        return getattr(result, "text", "") or ""
    except Exception as e:  # noqa: BLE001
        log.warning("doc_chat one-shot failed (model %s): %s — falling back to cascade", model_id, e)
        decision = route(
            db, task="validate",
            messages=[
                gateway.Message(role="system", content=system),
                gateway.Message(role="user", content=fallback_user),
            ],
        )
        return decision.text or ""


# ──────────────────────────────────────────────────────────────────────────
# M44.P3.A · DETERMINISTIC facts path · zero LLM calls.
#
# The existing `try_answer_from_facts` (below) calls the LLM to interpret
# the JSON blob — it still spends ~300 output tokens per answer. This
# function runs FIRST and tries to answer common factual questions using
# pure regex intent detection + dict lookup. Returns (answer, citations)
# or (None, []) when nothing matched.
#
# What's covered (and adds zero LLM cost):
#   "what is the/his/her aadhaar number?"      → fields.aadhaar_no | aadhaar
#   "what is the passport number?"             → fields.passport_no
#   "what is the PAN?"                         → fields.pan
#   "what is the NRIC / FIN?"                  → fields.nric / fin
#   "what is the date of birth / DOB?"         → fields.dob
#   "what is the name on this <doc>?"          → fields.full_name / name
#   "what is the address?"                     → fields.address
#   "when does it expire?"                     → fields.expiry_date / valid_thru
#   "when was it issued?"                      → fields.issue_date / effective_date
#   "who issued it?"                           → fields.issuer / issued_by
#   "is it signed?"                            → presence check on signature_blocks
#   "who signed it?"                           → signature_blocks[*].name
#   "what's the invoice/grand total?"          → fields.total / grand_total / amount
#   "what's the invoice number?"               → fields.invoice_number
#
# Every match here is a question that previously cost ~1 LLM call.
# ──────────────────────────────────────────────────────────────────────────

# (intent_name, regex_patterns, list of field-paths to try in order).
# Field paths use dot notation; "[]" means walk the array and return first
# non-empty string member. Patterns are case-insensitive and matched with
# re.search so phrasings ("what's the X", "tell me the X", "X please") all hit.
_FACT_INTENTS: list[tuple[str, list[str], list[str]]] = [
    # IDs — government / business. Field-path list includes the
    # canonical name AND common variants the classifiers emit. The
    # generic `national_id_number` / `document_number` / `id_number`
    # fallbacks catch the Aadhaar/NRIC/etc. extractors that don't emit
    # the type-specific field name.
    ("aadhaar",      [r"\baadhaar\b.*\b(number|no|id)\b",
                      r"\baadhaar\s*(?:number|no|id)\b",
                      r"^aadhaar$"],
                     ["aadhaar_no", "aadhaar.aadhaar_no", "aadhaar_number",
                      "national_id_number", "document_number", "id_number"]),
    ("passport",     [r"\bpassport\b.*\b(number|no|id)\b",
                      r"\bpassport\s*(?:number|no|id)\b"],
                     ["passport_no", "passport_number",
                      "document_number", "id_number"]),
    ("pan",          [r"\bpan\b\s*(?:number|no|card)?\b",
                      r"\bpermanent\s+account\s+number\b"],
                     ["pan", "pan_no", "pan_number", "document_number"]),
    ("nric",         [r"\bnric\b", r"\bfin\b\s*(?:number|no)?"],
                     ["nric", "fin", "id_number", "national_id_number", "document_number"]),
    ("dl",           [r"\bdriv(?:er|ing)\s+licen[cs]e\b.*\b(number|no|id)\b",
                      r"\bdl\s*(?:number|no)\b"],
                     ["dl_no", "licence_no", "license_number", "document_number"]),
    ("uen",          [r"\buen\b"], ["uen", "uen_no"]),
    ("gstin",        [r"\bgstin\b"], ["gstin", "gst_number"]),
    # Personal data
    ("dob",          [r"\bdate\s+of\s+birth\b", r"\bdob\b", r"\bborn\s+on\b"],
                     ["dob", "date_of_birth", "birth_date"]),
    ("name",         [r"\bname\s+on\b",          # "name on the document" / "name on this aadhaar"
                      r"^who(?:'s|\s+is)\s+(?:this|the\s+(?:holder|subject|applicant))\b",
                      r"\bholder(?:'s)?\s+name\b",
                      r"\bsubject(?:'s)?\s+name\b",
                      r"^(?:what(?:'s|\s+is)\s+(?:the\s+)?name|name)$"],
                     ["holder_name", "full_name", "name",
                      "applicant_name", "subject_name", "account_holder"]),
    ("sex",          [r"\b(?:sex|gender)\b"],
                     ["sex", "gender"]),
    ("address",      [r"\baddress\b(?!.*book)"],
                     ["address", "residential_address", "permanent_address"]),
    # Validity. "When was X issued" needs to match the passive — the
    # noun "issue" is far from the verb "was".
    ("expiry",       [r"\b(?:expir(?:y|es?|ation)|valid\s+(?:thru|until|till))\b",
                      r"\bwhen\s+does\s+(?:it|this)\s+expire\b"],
                     ["expiry_date", "valid_thru", "valid_until",
                      "expiration_date", "date_of_expiry"]),
    ("issued",       [r"\b(?:issue\s+date|issued\s+on|date\s+of\s+issue|effective\s+date)\b",
                      r"\bwhen\s+(?:was|did)\s+(?:it|this|the\s+\w+)\s+issued\b",
                      r"\bissue(?:d)?\s+date\b"],
                     ["issue_date", "effective_date", "issued_on",
                      "date_of_issue", "issuance_date"]),
    ("issuer",       [r"\b(?:issuer|issued\s+by|issuing\s+(?:authority|country|state))\b",
                      r"\bwho\s+issued\b"],
                     ["issuer", "issued_by", "issuing_authority",
                      "issuing_country", "issuing_state"]),
    ("country",      [r"\b(?:country|nationality|citizenship)\b"],
                     ["country", "nationality", "issuing_country"]),
    # Signatures
    ("signed",       [r"\bis\s+(?:it|this|the\s+document)\s+signed\b",
                      r"\bsignature\s+present\b",
                      r"\bsigned\??$"],
                     []),  # special handling — checks signature_blocks presence
    ("signatories",  [r"\bwho\s+signed\b", r"\bsignator(?:y|ies)\b"],
                     []),  # special — list signature_blocks[*].name
    # Money / invoice
    ("total",        [r"\b(?:grand|invoice|total)\s+(?:total|amount|due|payable)\b",
                      r"\bwhat(?:'s|\s+is)\s+the\s+total\b"],
                     ["grand_total", "total", "total_amount", "amount_due", "invoice_total"]),
    ("invoice_no",   [r"\binvoice\s+(?:number|no|id)\b"],
                     ["invoice_number", "invoice_no", "invoice_id"]),
]


# M44.P9.8 · Temporal-reasoning helpers ────────────────────────────────────
_TEMPORAL_PATTERNS = {
    "still_valid": [
        r"\bstill\s+valid\b",
        r"\bcurrently\s+valid\b",
        r"\bis\s+(?:it|this)\s+valid\b",
        r"\bin\s+force\b",
        r"\bactive\b\s+(?:still|now|currently)",
        r"\bcoverage\s+(?:still\s+)?(?:in\s+effect|active)\b",
    ],
    "has_expired": [
        r"\bhas\s+(?:it|this)\s+expired\b",
        r"\b(?:is|has)\s+(?:it|this)\s+(?:already\s+)?expired\b",
        r"\bexpired\?$",
    ],
    "days_until_expiry": [
        r"\bhow\s+(?:many\s+)?days?\s+(?:until|to|before)\s+(?:it|this|the)?\s*expir",
        r"\bdays?\s+(?:left|remaining)\s+(?:until|before)?\s*expir",
        r"\btime\s+(?:left|remaining|until)\s+expir",
    ],
}


def _parse_date_loose(s: str):
    """Parse common date formats. Returns date object or None."""
    import datetime as _dt
    if not s or not isinstance(s, str):
        return None
    s = s.strip()
    # Try standard formats most-common-first
    fmts = [
        "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y",
        "%d-%m-%Y", "%m-%d-%Y",
        "%d %b %Y", "%d %B %Y",
        "%b %d, %Y", "%B %d, %Y",
        "%d-%b-%y", "%d-%B-%y",
    ]
    for fmt in fmts:
        try:
            return _dt.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _find_expiry_date(fields: dict):
    """Search the curated + universal field names that typically hold
    expiration. Returns (date_obj, original_str) or (None, None)."""
    candidates = [
        "expiry_date", "valid_until", "valid_thru", "valid_till",
        "expiration_date", "date_of_expiry", "expires_on",
        "policy_end_date", "coverage_end_date", "end_date",
        "due_date",   # for invoices · 'expired' question still meaningful
    ]
    for key in candidates:
        v = fields.get(key)
        if isinstance(v, str) and v.strip():
            d = _parse_date_loose(v.strip())
            if d:
                return d, v.strip()
    # Also scan universal arrays
    for arr_name in ("dates", "key_facts"):
        for item in (fields.get(arr_name) or []):
            if not isinstance(item, dict):
                continue
            label = (item.get("label") or "").lower()
            if any(k in label for k in ("expir", "valid_until", "valid_thru", "end_date")):
                value = item.get("value")
                if isinstance(value, str):
                    d = _parse_date_loose(value)
                    if d:
                        return d, value
    return None, None


def _temporal_intent(fields: dict, q_lower: str) -> str | None:
    """Match a temporal-reasoning intent against the question and
    answer it via date arithmetic (zero LLM). Returns None when no
    match or no extractable expiry date."""
    intent = None
    for name, patterns in _TEMPORAL_PATTERNS.items():
        if any(_re.search(pat, q_lower) for pat in patterns):
            intent = name
            break
    if intent is None:
        return None

    expiry, expiry_str = _find_expiry_date(fields)
    if expiry is None:
        return None

    import datetime as _dt
    today = _dt.date.today()
    delta = (expiry - today).days

    if intent == "still_valid":
        if delta >= 0:
            return f"Yes — valid until {expiry_str} ({delta} days from today)."
        return f"No — expired on {expiry_str} ({-delta} days ago)."

    if intent == "has_expired":
        if delta < 0:
            return f"Yes — expired on {expiry_str} ({-delta} days ago)."
        return f"No — still valid; expires on {expiry_str} ({delta} days from today)."

    if intent == "days_until_expiry":
        if delta >= 0:
            return f"{delta} days until expiry on {expiry_str}."
        return f"Already expired {-delta} days ago on {expiry_str}."

    return None


def _walk_path(fields: dict, path: str):
    """Walk a dotted path into the fields dict. Returns the leaf value or None."""
    cur = fields
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def _format_value(v) -> str | None:
    """Render a leaf value as a clean answer string. Skips empty/None."""
    if v is None:
        return None
    if isinstance(v, str):
        v = v.strip()
        return v if v else None
    if isinstance(v, (int, float, bool)):
        return str(v)
    if isinstance(v, list):
        items = [_format_value(x) for x in v]
        return ", ".join(x for x in items if x) or None
    if isinstance(v, dict):
        # try common sub-keys
        for k in ("value", "text", "number", "name"):
            if k in v:
                inner = _format_value(v[k])
                if inner:
                    return inner
    return None


def try_answer_from_facts_deterministic(
    doc: Document,
    question: str,
) -> tuple[str | None, list[dict]]:
    """Pattern-match the question against known intents, then look up the
    answer directly in `extracted_fields.fields`. Pure regex + dict —
    ZERO LLM calls. Returns (answer, citations) or (None, []) on miss.

    Citations come from `field_bboxes` when present, otherwise from the
    extractor's chunk_refs (signature pages, intro chunks).

    On hit, the caller persists meta="facts_det" so the cache-stats
    endpoint counts this as a zero-LLM answer.
    """
    ef = doc.extracted_fields or {}
    fields = ef.get("fields") if isinstance(ef, dict) else None
    if not fields or not isinstance(fields, dict):
        return None, []

    q = (question or "").strip().lower()
    if not q:
        return None, []

    field_bboxes = ef.get("field_bboxes") or {}
    chunk_refs = ef.get("chunk_refs") or []

    # M44.P9.8 · Temporal reasoning intents. Pure date arithmetic, zero
    # LLM. Answers 'is this still valid?', 'has it expired?', 'how many
    # days until expiry?'. Reads expiry_date / valid_until / valid_thru
    # / expiration_date from extracted_fields.
    temporal_answer = _temporal_intent(fields, q)
    if temporal_answer:
        return (
            temporal_answer,
            _build_citations_from_extractor(field_bboxes, chunk_refs, "expiry_date"),
        )

    # M44.P7 · multi-intent guard. When a question asks for multiple
    # things, the greedy first-match returns only one. Two signals
    # tell us a question is multi-part:
    #   (a) ≥ 2 of our intent patterns match (covered case)
    #   (b) the question contains ≥ 2 question-words (what/who/when/
    #       where/how/which), e.g. 'who is the buyer AND what is the
    #       total?'. Even when only one intent matches, the second
    #       half is probably about something we don't have a pattern
    #       for (e.g. 'buyer' / 'address' on certain doc types).
    # Either signal defers to LLM.
    matched_intents = [
        intent for intent, patterns, _ in _FACT_INTENTS
        if any(_re.search(pat, q) for pat in patterns)
    ]
    if len(matched_intents) >= 2:
        return None, []
    q_words = _re.findall(r"\b(?:what|who|when|where|how|which|why)\b", q)
    if len(q_words) >= 2:
        return None, []

    for intent, patterns, field_paths in _FACT_INTENTS:
        if not any(_re.search(pat, q) for pat in patterns):
            continue

        # Special cases ────────────────────────────────────────────────────
        if intent == "signed":
            sigs = fields.get("signature_blocks") or fields.get("signatures") or []
            if isinstance(sigs, list) and any(
                isinstance(s, dict) and (s.get("name") or s.get("signatory") or s.get("date"))
                for s in sigs
            ):
                return (
                    f"Yes — the document is signed. {len(sigs)} signature block"
                    f"{'s' if len(sigs) != 1 else ''} present.",
                    _build_citations_from_extractor(field_bboxes, chunk_refs, "signature_blocks"),
                )
            # No signatures found
            return ("No — no signature blocks were extracted from this document.", [])

        if intent == "signatories":
            sigs = fields.get("signature_blocks") or fields.get("signatures") or []
            if isinstance(sigs, list) and sigs:
                names = []
                for s in sigs:
                    if isinstance(s, dict):
                        nm = s.get("name") or s.get("signatory") or s.get("signed_by")
                        if nm:
                            names.append(str(nm).strip())
                if names:
                    return (
                        f"Signed by: {', '.join(names)}.",
                        _build_citations_from_extractor(field_bboxes, chunk_refs, "signature_blocks"),
                    )
            return (None, [])

        # Standard path lookup. For ID intents, validate the value's format
        # against the expected pattern before accepting it · prevents
        # returning a Virtual ID (16-digit) when the user asked for an
        # Aadhaar (12-digit), and similar mismatches across passport / PAN
        # / NRIC / etc.
        id_intent_format = _ID_INTENT_FORMATS.get(intent)
        candidates: list[tuple[str, str]] = []  # (path, formatted_value)
        for path in field_paths:
            v = _walk_path(fields, path)
            formatted = _format_value(v)
            if formatted:
                candidates.append((path, formatted))

        if not candidates:
            continue

        # If this is an ID intent, prefer the candidate that matches the
        # expected regex. Fall back to first candidate when no match.
        chosen_path, chosen_value = candidates[0]
        if id_intent_format:
            for path, value in candidates:
                if _re.match(id_intent_format, value.strip()):
                    chosen_path, chosen_value = path, value
                    break

        ans = _format_answer_for_intent(intent, chosen_value)
        return ans, _build_citations_from_extractor(field_bboxes, chunk_refs, chosen_path)

    # M44.P8 · UNIVERSAL-extractor fallback. When a doc was extracted via
    # the universal schema (any non-curated doc type), its content lives
    # in typed arrays: dates[], amounts[], identifiers[], key_facts[].
    # Scan them for label-tokens matching the question.
    universal_hit = _search_universal_arrays(fields, q)
    if universal_hit:
        label, value = universal_hit
        return (
            f"{label.replace('_', ' ').strip().capitalize()}: {value}",
            _build_citations_from_extractor(field_bboxes, chunk_refs, label),
        )

    return None, []


def _search_universal_arrays(fields: dict, q_lower: str) -> tuple[str, str] | None:
    """When the doc was extracted via the 'universal' schema, its facts
    live in `dates`/`amounts`/`identifiers`/`key_facts` arrays each with
    {label, value}. Search them for label-tokens matching the question.

    Returns (label, value) on hit, or None when no array entry matches.
    """
    import re as _re
    stopwords = {
        "the", "a", "an", "is", "are", "was", "were", "be",
        "what", "whats", "when", "where", "who", "how", "much", "many",
        "this", "that", "these", "those", "of", "in", "on", "at", "to",
        "for", "with", "and", "or", "but", "do", "does", "did",
        "tell", "me", "please", "show", "give", "list", "summarize",
    }
    tokens = [
        t.lower() for t in _re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}", q_lower)
        if t.lower() not in stopwords and len(t) >= 3
    ]
    if not tokens:
        return None

    # Direct singletons first (primary_date / primary_amount / title /
    # issuer / subject_or_recipient) when their name is in tokens.
    # NOTE: match key words against the question's WHOLE-WORD tokens, never as
    # substrings of q_lower. The old substring check false-matched the fragment
    # "or" (from subject_OR_recipient) inside ordinary words — "Singapore",
    # "color", "according" — so off-topic questions ("what's the weather?")
    # leaked a stray field instead of abstaining. `tokens` already drops
    # stopwords + sub-3-char fragments (so "or"/"to"/"in" can't trigger).
    for label_key in ("title", "issuer", "subject_or_recipient",
                      "primary_date", "primary_amount"):
        v = fields.get(label_key)
        if isinstance(v, str) and v.strip():
            words_in_key = [w for w in label_key.replace("_", " ").split()
                            if len(w) >= 3 and w not in stopwords]
            if any(w in tokens for w in words_in_key):
                return label_key, v.strip()

    arrays = ("identifiers", "key_facts", "amounts", "dates")
    best: tuple[int, str, str] | None = None  # (score, label, value)
    for arr_name in arrays:
        items = fields.get(arr_name) or []
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            label = (item.get("label") or "").lower().strip()
            value = item.get("value")
            if not label or not isinstance(value, str) or not value.strip():
                continue
            # Split on underscore + non-word chars · `\w` includes `_`
            # in Python regex, so loan_number ≠ ["loan","number"]
            # without an explicit replace.
            label_words = set(label.replace("_", " ").replace("-", " ").split())
            score = sum(1 for t in tokens if t in label_words or t == label)
            if score > 0 and (best is None or score > best[0]):
                best = (score, label, value.strip())

    if best:
        return best[1], best[2]
    return None


# Format guards for ID intents · returned value must match this regex
# to be preferred over alternatives. Without these, a 16-digit Virtual
# ID extracted into `national_id_number` shadows the 12-digit Aadhaar
# in `document_number`.
_ID_INTENT_FORMATS: dict[str, str] = {
    "aadhaar":  r"^\s*\d{4}\s+\d{4}\s+\d{4}\s*$",
    "passport": r"^\s*[A-Z]\d{6,8}\s*$",
    "pan":      r"^\s*[A-Z]{5}\d{4}[A-Z]\s*$",
    "nric":     r"^\s*[STFGM]\d{7}[A-Z]\s*$",
    "ssn":      r"^\s*\d{3}-\d{2}-\d{4}\s*$",
    "ein":      r"^\s*\d{2}-\d{7}\s*$",
    "duns":     r"^\s*\d{9}\s*$",
    "gstin":    r"^\s*[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][0-9A-Z]Z[0-9A-Z]\s*$",
}


def _format_answer_for_intent(intent: str, value: str) -> str:
    """Wrap the raw value in a natural answer sentence based on intent."""
    label = {
        "aadhaar":     "The Aadhaar number is",
        "passport":    "The passport number is",
        "pan":         "The PAN is",
        "nric":        "The NRIC/FIN is",
        "dl":          "The driver licence number is",
        "uen":         "The UEN is",
        "gstin":       "The GSTIN is",
        "dob":         "Date of birth:",
        "name":        "Name on document:",
        "sex":         "Sex/gender:",
        "address":     "Address:",
        "expiry":      "Expires on:",
        "issued":      "Issued on:",
        "issuer":      "Issued by:",
        "country":     "Country:",
        "total":       "Total:",
        "invoice_no":  "Invoice number:",
    }.get(intent, "")
    return f"{label} {value}".strip()


def _build_citations_from_extractor(
    field_bboxes: dict,
    chunk_refs: list,
    field_hint: str | None = None,
) -> list[dict]:
    """Compact citation builder for the deterministic path.

    Prefers field_bboxes matching the field hint (best · gives a tight
    yellow box on the right page). Falls back to chunk_refs which point
    at the extractor's source pages. Caps at 3 citations to keep the
    UI focused — deterministic answers are short, don't need 6 sources.
    """
    out: list[dict] = []
    seen_pages: set[int] = set()
    # Field-bbox path · matches on field_hint substring (e.g. "aadhaar_no"
    # matches a bbox stored as "aadhaar_no" or "aadhaar.aadhaar_no")
    if field_bboxes and field_hint:
        for fname, bb in field_bboxes.items():
            if field_hint in fname or fname in field_hint:
                pg = bb.get("page", 1)
                entry: dict = {
                    "chunkPk": bb.get("chunk_pk") or 0,
                    "page": pg,
                    "bbox": None,
                    "fieldName": fname,
                }
                if "x0" in bb:
                    entry["bbox"] = {"page": pg, "x0": bb["x0"], "y0": bb["y0"],
                                     "x1": bb["x1"], "y1": bb["y1"]}
                out.append(entry)
                seen_pages.add(pg)
                if len(out) >= 3:
                    return out
    # Chunk-ref fallback
    for ref in chunk_refs[:8]:
        if len(out) >= 3:
            break
        pg = ref.get("page", 1)
        if pg in seen_pages:
            continue
        pk = ref.get("chunk_pk")
        if pk:
            out.append({"chunkPk": pk, "page": pg,
                        "bbox": ref.get("bbox") if ref.get("bbox") else None})
            seen_pages.add(pg)
    return out


def _backfill_citation_bboxes(db, document_pk: int, citations: list[dict]) -> None:
    """Mutate citations in-place: for any entry missing a bbox, look up the
    chunk's stored bbox (by chunkPk first, then by document+page as a fallback
    for stale PKs from re-ingestion). Without bbox the frontend falls back to
    fuzzy text-search highlighting — with it, PdfDocumentViewer draws a precise
    solid-gold box."""
    if not citations:
        return
    from app.orm import DocumentChunk
    from sqlalchemy import select as _sel

    # Direct PK lookup
    pks = [c["chunkPk"] for c in citations if c.get("chunkPk") and not c.get("bbox")]
    bbox_by_pk: dict[int, dict] = {}
    if pks:
        rows = db.execute(
            _sel(DocumentChunk.pk, DocumentChunk.bbox).where(DocumentChunk.pk.in_(pks))
        ).all()
        bbox_by_pk = {r.pk: r.bbox for r in rows if r.bbox}

    # (document, page) fallback for stale PKs
    need_fallback = [c for c in citations if not c.get("bbox") and c.get("page")]
    page_bbox: dict[int, dict] = {}
    if need_fallback:
        pages = {c["page"] for c in need_fallback if c.get("page", 0) > 0}
        if pages:
            rows2 = db.execute(
                _sel(DocumentChunk.page, DocumentChunk.bbox)
                .where(DocumentChunk.document_pk == document_pk,
                       DocumentChunk.page.in_(pages),
                       DocumentChunk.bbox.isnot(None))
                .order_by(DocumentChunk.chunk_index)
            ).all()
            for r in rows2:
                if not r.bbox:
                    continue
                if r.page not in page_bbox:
                    page_bbox[r.page] = dict(r.bbox)
                else:
                    # Union all chunk bboxes on this page so the fallback
                    # covers the full content region, not just the first chunk.
                    cur = page_bbox[r.page]
                    bb = r.bbox
                    if "x0_pct" in bb:   # percentage-based bbox
                        cur["x0_pct"] = min(cur.get("x0_pct", 1), bb["x0_pct"])
                        cur["x1_pct"] = max(cur.get("x1_pct", 0), bb["x1_pct"])
                        cur["y0_pct"] = min(cur.get("y0_pct", 1), bb["y0_pct"])
                        cur["y1_pct"] = max(cur.get("y1_pct", 0), bb["y1_pct"])
                    elif "x0" in bb:      # absolute-coord bbox
                        cur["x0"] = min(cur.get("x0", 1e9), bb["x0"])
                        cur["x1"] = max(cur.get("x1", 0), bb["x1"])
                        cur["y0"] = min(cur.get("y0", 1e9), bb["y0"])
                        cur["y1"] = max(cur.get("y1", 0), bb["y1"])

    for c in citations:
        if not c.get("bbox"):
            if c.get("chunkPk") is not None:
                c["bbox"] = bbox_by_pk.get(c["chunkPk"])
            if not c.get("bbox") and c.get("page"):
                c["bbox"] = page_bbox.get(c["page"])


def try_answer_from_facts(
    db: Session,
    doc: Document,
    question: str,
) -> tuple[str | None, list[dict]]:
    """Layer 1 fast path: if the doc has structured `extracted_fields`, ask
    the LLM to answer the question USING ONLY those facts.

    Returns (answer, citations). When the facts don't cover the question the
    answer is None and the caller falls back to the retrieval path. The
    structured facts replace the old signature-regex hack — signature
    blocks, effective dates, parties, totals etc. are all addressable from
    the facts JSON without any per-intent string matching.

    Citations come from the extractor's chunk_refs (which chunks fed the
    extraction), so the reviewer can trace the answer back to source pages.
    """
    ef = doc.extracted_fields or {}
    fields = ef.get("fields") if isinstance(ef, dict) else None
    if not fields:
        return None, []

    schema_key = ef.get("doc_type") or "document"
    confidence = float(ef.get("confidence", 0.0))
    chunk_refs = ef.get("chunk_refs") or []
    field_bboxes = ef.get("field_bboxes") or {}

    system = (
        "You answer questions about a single document using ONLY the "
        "STRUCTURED FACTS below — a JSON blob extracted by an earlier "
        "agent. Rules:\n"
        "- If the answer is directly in the facts, answer it in 1-3 "
        "tight sentences, quoting specific values (names, dates, "
        "amounts) verbatim.\n"
        "- For yes/no questions ('is this signed?'), answer Yes/No "
        "and quote the supporting value (signatory name + date).\n"
        "- If the question genuinely requires text the facts don't "
        "cover — open-ended interpretation, clause-by-clause analysis, "
        "definitions — reply with EXACTLY the single token "
        f"{FACTS_NOT_FOUND_SENTINEL} and nothing else.\n"
        "- Never invent. Never apologise. Never explain that you're "
        "using facts. Just answer or emit the sentinel."
    )
    user_block = (
        f"Document type: {schema_key} (extractor confidence {confidence:.2f})\n\n"
        f"STRUCTURED FACTS (JSON):\n{json.dumps(fields, indent=2, default=str)}\n\n"
        f"Question: {question}"
    )

    try:
        raw = llm_one_shot(db, system, user_block, max_tokens=300).strip()
    except Exception as e:  # noqa: BLE001
        log.warning("facts-first: LLM call failed, falling through: %s", e)
        return None, []

    if not raw or FACTS_NOT_FOUND_SENTINEL in raw:
        return None, []

    answer_lower = (raw or "").lower()

    def _field_relevance(field_name: str) -> int:
        base = field_name.lower().replace("_", " ")
        score = 0
        if base in answer_lower:
            score += 2
        v = fields.get(field_name) if "[" not in field_name else None
        if isinstance(v, str) and len(v) >= 4 and v.lower() in answer_lower:
            score += 5
        if "[" in field_name:
            arr_name, idx_part = field_name.split("[", 1)
            try:
                idx = int(idx_part.rstrip("]"))
                item = (fields.get(arr_name) or [])[idx]
                if isinstance(item, dict):
                    for k, sv in item.items():
                        if isinstance(sv, str) and len(sv) >= 4 and sv.lower() in answer_lower:
                            score += 5
                            break
            except (ValueError, IndexError):
                pass
        return score

    citations: list[dict] = []
    pages_cited: set[int] = set()

    if field_bboxes:
        def _sort_key(item):
            name, bb = item
            return (
                -_field_relevance(name),
                0 if "x0" in bb else 1,
                bb.get("page", 99),
                name,
            )
        ordered = sorted(field_bboxes.items(), key=_sort_key)
        for fname, bb in ordered[:6]:
            entry: dict = {
                "chunkPk": bb.get("chunk_pk") or 0,
                "page": bb.get("page", 1),
                "bbox": None,
                "quote": f"{fname}: {fields.get(fname.split('[')[0]) if '[' not in fname else (fields.get(fname.split('[')[0]) or [])[int(fname.split('[')[1].rstrip(']'))]}"[:180],
                "fieldName": fname,
            }
            if "x0" in bb:
                entry["bbox"] = {
                    "page": bb["page"],
                    "x0": bb["x0"], "y0": bb["y0"],
                    "x1": bb["x1"], "y1": bb["y1"],
                }
            citations.append(entry)
            pages_cited.add(bb.get("page", 1))

    seen_pks: set[int] = set()
    backfill_pool = list(chunk_refs)
    backfill_pool.sort(key=lambda r: 0 if r.get("page") not in pages_cited else 1)
    for ref in backfill_pool[:12]:
        if len(citations) >= 6:
            break
        pk = ref.get("chunk_pk")
        if not pk or pk in seen_pks:
            continue
        chunk = db.scalar(select(DocumentChunk).where(DocumentChunk.pk == pk))
        if chunk is None:
            continue
        quote = " ".join((chunk.text or "").split())[:180]
        citations.append({
            "chunkPk": chunk.pk,
            "page": chunk.page,
            "bbox": chunk.bbox,
            "quote": quote,
        })
        seen_pks.add(pk)
        pages_cited.add(chunk.page)

    return raw, citations


# ── M43.P1.5.D · Reflexion memory · few-shot retrieval ───────────────────
# Moved from routers/doc_chat.py to break a router→agent→router import cycle.


def build_reflexion_few_shot(db: Session, question: str) -> str:
    """Cosine-search reflexion_pairs for top-3 similar past questions
    whose critique was marked HELPFUL by reviewers (or net positive).
    Returns a "Common mistakes to avoid" preamble for the validator's
    system prompt, or empty string when no good signal exists yet.

    Filter rules:
      * helpful_count > marked_unhelpful_count    (avoid noise critiques)
      * passed_on_first == False                  (only learn from misses)
      * critique IS NOT NULL                      (need the explanation)
      * tenant-scoped via current tenant context

    Fails open: any error returns "" and the validator runs unchanged.
    """
    try:
        from app.db import get_current_tenant
        from app.documents_scope import get_current_owner_user_pk
        from app.embeddings import embed as _embed_fn
        from sqlalchemy import text as _sql_text
        tenant_id = get_current_tenant()
        [q_vec] = _embed_fn([question])

        # pgvector <=> cosine distance · ASC = closer = more similar
        # Format the vector as pgvector text literal for direct bind
        vec_lit = "[" + ",".join(f"{v:.6f}" for v in q_vec) + "]"
        # M46 · §4 · owner scope · don't inject another user's critiques as
        # few-shot hints (documents product). NULL owner (auditing) → no filter.
        # SAFETY: every clause in `where_parts` is a hardcoded trusted string;
        # the conditional owner filter only adds another hardcoded clause with a
        # bound parameter — no user input ever enters the SQL template.
        _owner = get_current_owner_user_pk()
        where_parts = [
            "tenant_id = :tid",
            "critique IS NOT NULL",
            "passed_on_first = false",
            "helpful_count >= marked_unhelpful_count",
        ]
        params = {"qv": vec_lit, "tid": tenant_id}
        if _owner is not None:
            where_parts.append("owner_user_id = :owner")
            params["owner"] = int(_owner)
        where_clause = "\n                   AND ".join(where_parts)
        rows = db.execute(
            _sql_text(f"""
                SELECT pk, question, critique, final_answer,
                       helpful_count, marked_unhelpful_count,
                       question_embed <=> :qv AS dist
                  FROM reflexion_pairs
                 WHERE {where_clause}
                 ORDER BY question_embed <=> :qv
                 LIMIT 3
            """),
            params,
        ).all()
        if not rows:
            return ""
        lines = ["Common mistakes prior reviewers caught on similar questions:"]
        for i, r in enumerate(rows, start=1):
            crit_short = (r.critique or "").strip().splitlines()[0][:200]
            fix_short = (r.final_answer or "").strip().splitlines()[0][:200]
            lines.append(f"  {i}. Mistake: {crit_short}")
            lines.append(f"     Correct: {fix_short}")
        lines.append("Use these as guidance — verify against the evidence excerpts below.")
        return "\n".join(lines)
    except Exception as e:  # noqa: BLE001
        log.debug("reflexion few-shot retrieval failed (non-fatal): %s", e)
        return ""
