"""Deterministic chat answer handlers — split out of workspace_chat.py.

The aggregation/lookup handlers (counts, money, entities, identity, watchlist, contracts, etc.) that
RAG/agent handle unreliably, plus the document-overview + deterministic_answer dispatcher. Pure
per-owner reads; owner scope comes from the ContextVar. workspace_chat imports deterministic_answer +
_is_broad_overview + _aggregate_overview from here (one-directional; no import back).
"""
from __future__ import annotations

import logging
import re
from collections import defaultdict
from datetime import date, datetime, timezone  # noqa: F401

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_current_tenant  # noqa: F401
from app.documents_scope import get_current_owner_user_pk
from app.orm import Document

log = logging.getLogger(__name__)


_BROAD_OVERVIEW_RX = re.compile(
    # "summarize" only counts as a WHOLE-LIBRARY ask when a library word follows — "summarise my
    # invoices" must fall through to RAG and summarise the invoices, not return the type tally.
    r"summari[sz]e\s+(?:(?:all|my|the|these)\s+)*(?:documents?|files?|records?|library|everything|stuff|data)\b"
    r"|\b(overview|inventory|"
    r"how many\s+(of\s+(my|the|your|these)\s+)?(documents?|files?|records?|papers?|things|types?|kinds?)|"
    r"what (do i have|documents|files)|"
    r"all (my |the )?(docs|documents|files|of them)|everything)\b", re.I)


# A trailing "... of/for/about/named <someone>" turns a whole-library ask into a
# FILTERED one ("all documents of kalyani") — that must NOT be answered with the
# everything-tally. Pronouns/determiners after the preposition ("all of them",
# "for the year") are not entity filters, so they're excluded.
_OVERVIEW_QUALIFIER_RX = re.compile(
    r"\b(of|for|about|named|belonging to|owned by|related to)\s+"
    r"(?!them\b|these\b|those\b|the\b|my\b|it\b|us\b|you\b|me\b|each\b|any\b|all\b)[a-z0-9]",
    re.I)

# A SPECIFIC analytical ask ("biggest amount across all my documents", "which companies appear",
# "is my name consistent", "what expires") is NOT the whole-library tally even though it says
# "all my documents". These words mean the query wants an ANSWER, not an inventory count.
# Prefixes (no trailing boundary, so 'compan' hits 'companies', 'expir' hits 'expires') + whole
# words (bounded, so 'sum' can't fire on 'summarize' and keep a real overview broad).
_ANALYTICAL_RX = re.compile(
    r"\b(?:compan|organi[sz]|expir|consisten|contradict|mismatch|renew|convert|transaction)"
    r"|\b(?:people|persons?|whose|biggest|largest|smallest|highest|lowest|most|least|total|sum|"
    r"combined|average|spend|spent|amount|balance|compare|comparison|versus|timeline|due|overdue|"
    r"conflict|match|rank|action|exposure|missing|mention)\b", re.I)


def _is_broad_overview(text: str) -> bool:
    """A whole-library INVENTORY question ('summarize all my documents', 'what do I have',
    'how many documents') → answer with a deterministic count-by-type aggregate. NOT a broad
    overview when a name/entity qualifier ('... of kalyani') filters it, or when a specific
    ANALYTICAL word means the user wants an actual answer, not an inventory tally."""
    t = text or ""
    return (bool(_BROAD_OVERVIEW_RX.search(t))
            and not _OVERVIEW_QUALIFIER_RX.search(t)
            and not _ANALYTICAL_RX.search(t))


def _aggregate_overview(db: Session, tenant_id: str) -> str:
    """Deterministic, SQL-only workspace overview (counts by type) — no LLM, no
    row loading, scales to ANY number of documents. Rendered as a table by the
    chat's RichMessage component."""
    uid = get_current_owner_user_pk()
    filters = [Document.tenant_id == tenant_id, Document.is_archived.is_(False)]
    if uid is not None:
        filters.append(Document.owner_user_id == uid)
    total = db.scalar(select(func.count()).select_from(Document).where(*filters)) or 0
    ready = db.scalar(select(func.count()).select_from(Document)
                      .where(*filters, Document.ingestion_status == "ready")) or 0
    by_type = db.execute(
        select(Document.doc_type, func.count()).where(*filters, Document.ingestion_status == "ready")
        .group_by(Document.doc_type).order_by(func.count().desc()).limit(20)
    ).all()

    lines = [f"You have **{total:,}** document(s) — **{ready:,}** processed and searchable.", ""]
    rows = [((t or "unclassified").replace("_", " "), c) for t, c in by_type if c]
    if rows:
        lines += ["| Document type | Count |", "|---|---|"]
        lines += [f"| {t} | {c:,} |" for t, c in rows]
    lines += [
        "",
        "That's a workspace-wide tally. For a *content* summary, narrow the ask — "
        "e.g. *“summarise my bank statements from 2026”* or tick specific documents — "
        "so the answer stays precise across a large library.",
    ]
    return "\n".join(lines)


# M46 · "list all <type>" questions are answered by CLASSIFICATION, not content
# RAG — RAG retrieves docs that merely mention an identifier (insurance cert with
# an NRIC) and misses the real one (an Aadhaar that never says "NRIC").
_TYPE_SYNONYMS: dict[str, tuple[str, ...]] = {
    "national id": ("national_id", "passport", "nric", "aadhaar", "identity", "id_card", "residence_permit", "driver", "licen"),
    "passport": ("passport",),
    "driver": ("driver", "licen"),
    "invoice": ("invoice",),
    "receipt": ("receipt",),
    "insurance": ("insurance",),
    "bank statement": ("bank_statement", "bank_account_statement", "account_statement"),
    "statement": ("statement",),
    "lease": ("lease", "tenancy", "rental"),
    "agreement": ("agreement", "contract"),
    "certificate": ("certificate", "cert"),
    "medical": ("medical", "lab", "test_result", "prescription", "discharge", "health"),
    "policy": ("policy", "procedure"),
}

def _doc_primary_date(d) -> str | None:
    """Best date for a doc: an extracted primary/issue/invoice date, else the upload date."""
    ef = d.extracted_fields or {}
    f = ef.get("fields") or {}
    for k in ("primary_date", "date", "invoice_date", "issue_date", "statement_date",
              "event_date", "transaction_date", "date_of_issue"):
        v = f.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()[:10]
    pd = ef.get("primary_date")
    if isinstance(pd, str) and pd.strip():
        return pd.strip()[:10]
    try:
        return d.created_at.date().isoformat()
    except Exception:  # noqa: BLE001
        return None


# Prefix stems (no trailing \b) so 'expir' matches expired/expiry/expiration, 'renew' → renewal/renews.
_WATCH_INTENT = re.compile(
    r"(renew|expir|lapse|overdue|deadline|upcoming|remind|\bdue\b|coming up|about to|"
    r"need\w*\s+to\s+(pay|renew|do)|what.*(need|should).*(pay|renew|do|watch)|"
    r"keep track|needs?\s+attention|what.*attention)", re.I)


def _rel_when(days: int) -> str:
    if days == 0:
        return "today"
    if days < 0:
        return f"{abs(days)} day{'' if days == -1 else 's'} ago"
    if days == 1:
        return "tomorrow"
    if days < 45:
        return f"in {days} days"
    if days < 400:
        return f"in {round(days / 30)} month{'' if round(days / 30) == 1 else 's'}"
    return f"in {days // 365} year{'' if days // 365 == 1 else 's'}"


def _answer_watchlist(db: Session, tenant_id: str, text: str) -> str | None:
    """Assistant intent — renewals / expiries / due dates / 'what needs attention'. Answers from the
    watchlist engine (deterministic, from extracted date fields), so the chat is assistant-aware."""
    if not _WATCH_INTENT.search(text or ""):
        return None
    try:
        from app.routers.assistant import _derive_items
        items = _derive_items(db)
    except Exception:  # noqa: BLE001
        return None
    if not items:
        return ("Nothing needs your attention right now — I don't see any upcoming renewals, "
                "expiries or due dates across your documents.")
    URG = {"overdue": "🔴 Overdue", "urgent": "🟠 This week", "soon": "🟡 This month",
           "upcoming": "🟣 Next 3 months", "info": "🟢 Later"}
    lines = ["Here's what your documents say needs attention:"]
    for u in ("overdue", "urgent", "soon", "upcoming", "info"):
        grp = [it for it in items if it["urgency"] == u]
        if not grp:
            continue
        lines.append(f"\n**{URG[u]}**")
        for it in grp[:12]:
            lines.append(f"- **{it['title']}** — {it['date']} ({_rel_when(it['daysUntil'])}) · _{it['docName']}_\n"
                         f"  {it['suggestion']}")
    lines.append("\n_Open the **Assistant** tab to add any of these as a calendar reminder (.ics)._")
    return "\n".join(lines)


# ── Entity aggregation (people / organisations across documents) ────────────────
_PERSON_KEYS = ("full_name", "cardholder_name", "signatory_name", "account_holder_name", "holder_name",
                "payee_name", "consignee_name", "shipper_name", "person_name", "applicant_name",
                "employee_name", "director_name", "owner_name", "trainee_name", "student_name",
                "candidate_name")
_ORG_KEYS = ("business_name", "company_name", "vendor_name", "bank_name", "platform_name",
             "service_provider_name", "supplier_name", "employer_name", "merchant_name", "organisation",
             "organization", "issuing_authority", "institution_name")
_AMBIGUOUS_KEYS = ("buyer_name", "seller_name", "recipient_name", "name")  # person OR org → decide by suffix
_NAME_ARRAY_KEYS = ("parties", "owners", "directors", "shareholders", "signatories", "additional_names")
_ORG_SUFFIX = re.compile(
    r"\b(pte|ltd|llc|inc|corp|co|gmbh|plc|pac|llp|group|holdings?|bank|company|limited|associates|"
    r"partners|technologies|systems|solutions|services|assurance|capital|ventures|enterprises?|"
    r"trading|pty|sdn|bhd|university|institute|school|hospital|clinic|agency|authority|foundation|"
    # extra org signals (org-as-person mis-classification): SA/branch/international/diagnostics/labs/…
    r"branch|international|incorporated|corporation|association|society|council|commission|consult\w*|"
    r"diagnostics?|laborator(?:y|ies)|labs?|centre|ministry|department|bureau|board)\b\.?",
    re.I)


def _classify_name(kl: str, nm: str) -> str | None:
    """→ 'person' | 'org' | None for a (field_key, name-value) pair."""
    if "address" in kl or "street" in kl or "location" in kl:
        return None
    is_org = bool(_ORG_SUFFIX.search(nm))
    if any(kl == pk or kl.endswith("_" + pk) for pk in _PERSON_KEYS):
        return "org" if is_org else "person"
    if any(ok in kl for ok in _ORG_KEYS) or is_org:
        return "org"
    if kl in _AMBIGUOUS_KEYS or kl in _NAME_ARRAY_KEYS:
        return "org" if is_org else "person"
    return None
_ENTITY_INTENT = re.compile(
    r"(which|what|list|any|find|show|who).{0,40}\b(document|doc|file|paper)s?\b.{0,40}"
    r"(about|mention|reference|contain|name|person|people|company|companies|organi[sz]ation|firm|business)"
    r"|(what|which|list|who).{0,30}(compan|organi[sz]ation|firm|business|people|persons|names|individuals)"
    r"|about (whom|who)|same (person|name|individual|entity)|whose (name|document)|"
    r"who (appears|is named|are the people)"
    # "documents with (the) name X" / "documents named X" — no lead verb needed. The
    # branch handlers self-gate (they need an extractable name), so this is safe to widen.
    r"|\b(document|doc|file|paper)s?\b[^.?!]{0,30}\bname[ds]?\b", re.I)


def _name_str(v) -> str | None:
    if isinstance(v, str):
        s = v.strip()
    elif isinstance(v, dict):
        s = str(v.get("name") or v.get("value") or v.get("entity") or v.get("party") or "").strip()
    else:
        return None
    if not s or len(s) < 2 or len(s) > 80:
        return None
    # a plausible name: has a letter, not a pure number / date / id token
    if not re.search(r"[A-Za-z]", s) or re.fullmatch(r"[\d\W]+", s):
        return None
    if s.upper() in ("N/A", "NA", "NONE", "NULL", "UNKNOWN", "-"):
        return None
    if re.fullmatch(r"\[[^\]]+\]", s):   # masked PII placeholder e.g. [PERSON_1]
        return None
    # Reject descriptive text (résumé skills/education, notes) — real names/orgs are short + no colon.
    if ":" in s or "\n" in s or len(s.split()) > 7:
        return None
    return s


def _collect_entities(db: Session):
    """Per owner-scoped doc → the person + organisation names in its EXTRACTED FIELDS (typed keys).
    Returns (people, orgs, per_doc) where people/orgs map name → set(docName)."""
    from app.documents_scope import get_current_owner_user_pk
    owner = get_current_owner_user_pk()
    if owner is None:
        return defaultdict(set), defaultdict(set), {}  # fail closed — no owner scope → no data
    q = select(Document).where(Document.ingestion_status == "ready", Document.owner_user_id == owner)
    docs = db.scalars(q).all()
    people: dict[str, set] = defaultdict(set)
    orgs: dict[str, set] = defaultdict(set)
    per_doc: dict[str, dict] = {}
    for d in docs:
        f = ((d.extracted_fields or {}).get("fields") or {})
        dp, do = set(), set()
        for k, v in f.items():
            kl = k.lower()
            vals = v if isinstance(v, list) else [v]
            for val in vals:
                nm = _name_str(val)
                if not nm:
                    continue
                cls = _classify_name(kl, nm)
                if cls == "person":
                    dp.add(nm)
                elif cls == "org":
                    do.add(nm)
        for nm in dp:
            people[nm].add(d.name)
        for nm in do:
            orgs[nm].add(d.name)
        per_doc[d.name] = {"people": dp, "orgs": do, "type": d.doc_type}
    return people, orgs, per_doc


def _answer_entities(db: Session, tenant_id: str, text: str) -> str | None:
    """Cross-document entity questions — 'which docs are about X', 'what companies/people appear',
    'documents with a person's name', 'any two about the same person'. Deterministic (no LLM)."""
    if not _ENTITY_INTENT.search(text or ""):
        return None
    tl = (text or "").lower()
    people, orgs, per_doc = _collect_entities(db)

    # (a) "documents ABOUT / that MENTION / with NAME <name>" — pull the name after the
    # keyword. Accept a QUOTED name ("Kalyani") or a Capitalized one; a quote otherwise
    # drops the query into the "list all people" branch (d) below and lists everyone.
    _kw = (r"\b(?:about|mention(?:ing|s)?|reference(?:s)?|contain(?:ing|s)?|of|for|named?|"
           r"called|with name|with the name)\s+")
    mq = re.search(_kw + r"[\"'“‘]([^\"'”’\n]{2,40})[\"'”’]", text or "", re.I)
    mc = re.search(_kw + r"([A-Z][\w.'-]+(?:\s+[A-Z][\w.'-]+){0,3})", text or "")
    if (mq or mc) and not re.search(r"\b(compan|organi|people|persons|firm|business)\b", tl):
        needle = (mq.group(1) if mq else mc.group(1)).strip()
        nl = needle.lower()
        hits = []
        # field entities
        for nm, ds in {**people, **orgs}.items():
            if nl in nm.lower() or nm.lower() in nl or _token_overlap(nl, nm.lower()):
                for dn in ds:
                    hits.append(dn)
        # entity graph (broader mention search across text)
        try:
            from app.orm import Entity
            rows = db.execute(
                select(Document.name).select_from(Entity)
                .join(Document, Document.pk == Entity.document_pk)
                .where(Entity.canonical.ilike(f"%{needle}%"))
            ).all()
            hits += [r[0] for r in rows]
        except Exception:  # noqa: BLE001
            pass
        hits = sorted(set(hits))
        if not hits:
            return (f"I couldn't find any documents that clearly mention **{needle}**. "
                    "Try the exact name as it appears on the document.")
        lines = [f"**{len(hits)} document(s)** mention **{needle}**:"]
        for dn in hits:
            info = per_doc.get(dn, {})
            lines.append(f"- **{dn}** — {(info.get('type') or 'document').replace('_', ' ')}")
        return "\n".join(lines)

    # (b) companies / organisations across all docs
    if re.search(r"\b(compan(y|ies)|organi[sz]ations?|firms?|businesses?)\b", tl):
        if not orgs:
            return "I don't see any company or organisation names in your documents yet."
        lines = [f"**{len(orgs)} organisation(s)** appear across your documents:"]
        for nm, ds in sorted(orgs.items(), key=lambda x: (-len(x[1]), x[0].lower())):
            lines.append(f"- **{nm}** — in {len(ds)} document(s): {', '.join(sorted(ds))}")
        return "\n".join(lines)

    # (c) "any two documents about the SAME person"
    if re.search(r"same (person|name|individual|entity)|two.*same|share.*(person|name)", tl):
        shared = {nm: ds for nm, ds in people.items() if len(ds) >= 2}
        if not shared:
            return "No single person appears across two or more of your documents."
        lines = ["Yes — these people appear in more than one document:"]
        for nm, ds in sorted(shared.items(), key=lambda x: -len(x[1])):
            lines.append(f"- **{nm}** — {len(ds)} documents: {', '.join(sorted(ds))}")
        return "\n".join(lines)

    # (d) people / names across all docs (incl. "documents with a person's name")
    if re.search(r"\b(people|persons?|names?|individuals?|whose)\b", tl):
        if not people:
            return "I don't see any personal names in your documents yet."
        lines = [f"**{len(people)} person(s)** named across your documents:"]
        for nm, ds in sorted(people.items(), key=lambda x: (-len(x[1]), x[0].lower())):
            lines.append(f"- **{nm}** — in {len(ds)} document(s): {', '.join(sorted(ds))}")
        return "\n".join(lines)

    return None


def _token_overlap(a: str, b: str) -> bool:
    ta, tb = set(re.findall(r"[a-z0-9]+", a)), set(re.findall(r"[a-z0-9]+", b))
    return len(ta & tb) >= 2 and len(ta & tb) >= min(len(ta), len(tb))


def _docs_mentioning_name(db: Session, tenant_id: str, needle: str,
                          people: dict, orgs: dict) -> set[str]:
    """All of the owner's document NAMES that mention a person/org `needle` — from the
    extracted-field entities, the entity graph, AND the shared keyword search (extracted
    fields JSONB + chunk text).  Three-source coverage means a name that appears only in
    a document's body text (not as a classified entity) is still found.  Shared by the
    entities handler and the LLM-routed name-query handler (so both stay consistent)."""
    nl = (needle or "").strip().lower()
    if len(nl) < 2:
        return set()
    hits: set[str] = set()

    # 1. Field entities — typed person/org keys in extracted_fields
    for nm, ds in {**people, **orgs}.items():
        nml = nm.lower()
        if nl in nml or nml in nl or _token_overlap(nl, nml):
            hits |= set(ds)

    # 2. Entity graph — formal Entity rows (NER pipeline)
    try:
        from app.orm import Entity
        owner = get_current_owner_user_pk()
        q = (select(Document.name).select_from(Entity)
             .join(Document, Document.pk == Entity.document_pk)
             .where(Entity.tenant_id == tenant_id, Entity.canonical.ilike(f"%{nl}%"),
                    Document.is_archived.is_(False), Document.ingestion_status == "ready"))
        if owner is not None:
            q = q.where(Document.owner_user_id == owner)
        hits |= {r[0] for r in db.execute(q).all()}
    except Exception:  # noqa: BLE001
        pass

    # 3. Shared keyword search — extracted_fields JSONB + chunk text + doc names.
    #    Catches names in generic field values and body text that weren't classified
    #    as person/org entities (e.g. "kalyani" in a non-standard field key or buried
    #    in a paragraph).  This is the same search Content search uses.
    try:
        from app.services.document_search import keyword_search_documents
        owner = get_current_owner_user_pk()
        if owner is not None:
            kw_results = keyword_search_documents(
                db, needle, tenant_id=tenant_id, owner_user_id=owner,
            )
            hits |= {r["name"] for r in kw_results}
    except Exception:  # noqa: BLE001
        pass

    return hits


def answer_name_query(db: Session, tenant_id: str, names: list[str],
                      want: str = "list", doc_type: str | None = None) -> str | None:
    """Answer a name-filtered document question with CLEAN names supplied by the LLM intent
    resolver (no brittle regex extraction). One name → its documents; several names → the
    INTERSECTION ('of the Rajesh docs, how many also mention Kalyani'). `want` count|list only
    changes the phrasing. Returns None when there are no names to act on."""
    names = [n.strip() for n in (names or []) if n and n.strip()][:4]
    if not names:
        return None
    people, orgs, per_doc = _collect_entities(db)
    sets = [(n, _docs_mentioning_name(db, tenant_id, n, people, orgs)) for n in names]
    common = set.intersection(*[s for _, s in sets]) if len(sets) > 1 else sets[0][1]
    if doc_type:
        stem = re.split(r"[^a-z0-9]+", doc_type.lower())[0]
        if stem:
            common = {d for d in common if stem in (per_doc.get(d, {}).get("type") or "").lower()}
    label = " and ".join(f"**{n}**" for n, _ in sets)
    if not common:
        if len(sets) > 1:
            # Which of the names actually appears — so the reply is useful, not a flat "none".
            present = [n for n, s in sets if s]
            if present and len(present) < len(sets):
                missing = [n for n, s in sets if not s]
                return (f"No document mentions {label} together. "
                        f"{' and '.join(missing)} — I don't find in your documents.")
            return f"No single document mentions {label} together."
        return f"I don't find any documents that mention {label} in your workspace."
    docs = sorted(common)
    dtn = (" · " + doc_type) if doc_type else ""
    head = (f"**{len(docs)}** document{'s' if len(docs) != 1 else ''} mention {label}{dtn}"
            + (":" if want != "count" else f" — **{len(docs)}** in total:"))
    lines = [head]
    for d in docs:
        t = (per_doc.get(d, {}).get("type") or "document").replace("_", " ")
        lines.append(f"- **{d}** — {t}")
    return "\n".join(lines)


def owner_doc_types(db: Session, tenant_id: str) -> list[str]:
    """Distinct classified doc types across the owner's ready documents — passed to the LLM
    intent layer so it can map a semantic category ('expense related') to the types actually
    present, and used by `answer_doc_type_query`."""
    owner = get_current_owner_user_pk()
    filters = [Document.tenant_id == tenant_id, Document.is_archived.is_(False),
               Document.ingestion_status == "ready"]
    if owner is not None:
        filters.append(Document.owner_user_id == owner)
    rows = db.execute(select(Document.doc_type).where(*filters).distinct()).all()
    return sorted({(r[0] or "").strip() for r in rows if r[0]})


def answer_doc_type_query(db: Session, tenant_id: str, doc_types: list[str],
                          want: str = "list", label: str | None = None) -> str | None:
    """List (or count) the owner's documents whose CLASSIFIED type is in `doc_types` — the set the
    LLM intent layer resolved from a category/theme ('expense related' → invoice, receipt, bank
    statement, credit card statement, customer payment, financial report). None → no such docs."""
    types_l = {t.strip().lower() for t in (doc_types or []) if t and t.strip()}
    if not types_l:
        return None
    owner = get_current_owner_user_pk()
    filters = [Document.tenant_id == tenant_id, Document.is_archived.is_(False),
               Document.ingestion_status == "ready"]
    if owner is not None:
        filters.append(Document.owner_user_id == owner)
    docs = [d for d in db.scalars(select(Document).where(*filters)).all()
            if (d.doc_type or "").strip().lower() in types_l]
    if not docs:
        return None
    docs.sort(key=lambda d: (d.doc_type or "", d.name or ""))
    what = f" {label}" if label else ""
    n = len(docs)
    # Lead with the plain count for a "how many" ask; a bare list otherwise.
    head = (f"You have **{n}**{what} document{'s' if n != 1 else ''}:" if want == "count"
            else f"**{n}**{what} document{'s' if n != 1 else ''}:")
    lines = [head] + [f"- **{d.name}** — {(d.doc_type or 'document').replace('_', ' ')}" for d in docs]
    return "\n".join(lines)


def _answer_count_or_dates(db: Session, tenant_id: str, text: str) -> str | None:
    """Deterministic answers for the aggregations the agent handles unreliably (it loops / hits the
    step limit): a TYPE-SPECIFIC count ('how many invoices' → 5) and OLDEST/NEWEST. SQL/data only —
    fast + always correct. Returns None when the question isn't one of these (→ agent / RAG)."""
    tl = (text or "").lower()
    uid = get_current_owner_user_pk()
    filters = [Document.tenant_id == tenant_id, Document.is_archived.is_(False),
               Document.ingestion_status == "ready"]
    if uid is not None:
        filters.append(Document.owner_user_id == uid)

    # oldest / newest — only when clearly about a single doc's recency
    if re.search(r"\b(oldest|newest|most recent|earliest|latest)\b", tl) and \
            re.search(r"\b(document|doc|file|one|upload|record)", tl):
        from app.routers.assistant import _parse_date
        docs = db.scalars(select(Document).where(*filters)).all()
        # Parse to a real date before sorting — a lexical string sort mis-orders DD/MM/YYYY vs YYYY-MM-DD
        # and would name the wrong document as oldest/newest.
        dated = []
        for d in docs:
            raw = _doc_primary_date(d)
            pd = _parse_date(raw) if raw else None
            if pd is not None:
                dated.append((d, raw, pd))
        if dated:
            dated.sort(key=lambda x: x[2])
            o, od = dated[0][0], dated[0][1]
            n, nd = dated[-1][0], dated[-1][1]
            if o.pk == n.pk:
                return f"You have one dated document: **{o.name}** ({od})."
            return f"Your **oldest** document is **{o.name}** ({od}); your **newest** is **{n.name}** ({nd})."

    # type-specific count: "how many <type> …" — answer ONLY when the phrase maps to a real type.
    m = re.search(r"how many\s+(.+?)(?:\s+(?:do|are|is|did|have|in|from|that|which|and)\b|\?|$)", tl)
    if m:
        phrase = m.group(1).strip().rstrip("?").strip().rstrip("s").strip()
        if phrase and phrase not in ("document", "doc", "file", "thing", ""):
            rows = db.execute(select(Document.doc_type, func.count()).where(*filters)
                              .group_by(Document.doc_type)).all()
            first = phrase.split()[0]
            # SUM across every doc_type the phrase matches — "how many statements"
            # spans bank_account_statement + credit_card_statement, so returning the
            # first matching group (old behavior) undercounts. Deterministic (no
            # reliance on GROUP BY order).
            matched = []
            for dt, c in rows:
                dtn = (dt or "").replace("_", " ")
                if dtn and (phrase == dtn or phrase == dtn.rstrip("s") or phrase in dtn or dtn.startswith(first)):
                    matched.append((dtn, c))
            if matched:
                total = sum(c for _, c in matched)
                if len(matched) == 1:
                    dtn = matched[0][0]
                    return f"You have **{total}** {dtn}{'' if total == 1 else 's'}."
                breakdown = ", ".join(f"{c} {dtn}" for dtn, c in sorted(matched, key=lambda x: -x[1]))
                return f"You have **{total}** documents matching “{phrase}” ({breakdown})."
            # a specific-but-unknown type → fall through (could be a content question, don't assert 0)
    return None


# TRANSACTIONAL money docs only — amounts that are comparable/summable. Deliberately excludes
# bank_statement / investment statements: those carry a BALANCE (e.g. closing_balance), which is a
# different concept from spend and would make a "combined total" nonsensical (a 1.2M balance summed
# with 11.60 invoices). Ask a bank statement directly (doc-chat) for its balance.
_MONEY_TYPES = ("invoice", "receipt", "credit_card_statement", "customer_payment", "proforma_invoice")


def _parse_amount(v) -> float | None:
    """Pull the monetary amount out of 4080.0 / '4,080.00' / 'SGD 4,080.00' /
    '4,080.00 (VAT 8%)' / '$100 x 2'.

    The naive 'last numeric token' is wrong: a trailing VAT %, item count, or
    multiplier wins over the real amount ('4,080.00 (VAT 8%)' -> 8). Instead prefer
    MONEY-SHAPED tokens (a thousands separator or a decimal point — a %/count/
    multiplier is rarely formatted that way) and take the largest; if none look
    monetary, take the largest numeric token (so '$100 x 2' -> 100, not 2)."""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if not isinstance(v, str):
        return None
    toks = re.findall(r"\d[\d,]*\.?\d*", v)
    vals = []
    for t in toks:
        try:
            vals.append((t, float(t.replace(",", ""))))
        except ValueError:
            continue
    if not vals:
        return None
    money = [n for t, n in vals if ("," in t or "." in t)]
    return max(money) if money else max(n for _, n in vals)


_AMOUNT_FIELDS = ("grand_total", "total", "total_due", "total_amount", "amount_due",
                  "invoice_total", "amount")
_CCY_SYMBOL = {"$": "USD", "€": "EUR", "£": "GBP", "¥": "JPY", "₹": "INR", "₩": "KRW"}
_CCY_CODE_RX = re.compile(r"\b(USD|EUR|GBP|SGD|JPY|CNY|INR|AUD|CAD|CHF|HKD|NZD|KRW|MYR|THB|AED)\b", re.I)


def _doc_amount(d) -> float | None:
    f = (d.extracted_fields or {}).get("fields") or {}
    # transactional amounts only — NOT balance/closing_balance (those are not spend).
    for k in _AMOUNT_FIELDS:
        a = _parse_amount(f.get(k))
        if a is not None:
            return a
    return None


def _doc_currency(d) -> str:
    """Best-effort ISO-ish currency code for a doc's amount, so totals can be kept
    per-currency (summing USD + SGD into one figure is meaningless). Reads an
    explicit currency field, else sniffs a symbol/code from the amount string;
    '' when unknown."""
    f = (d.extracted_fields or {}).get("fields") or {}
    for k in ("currency", "currency_code", "ccy"):
        v = f.get(k)
        if isinstance(v, str) and v.strip():
            s = v.strip()
            if s in _CCY_SYMBOL:
                return _CCY_SYMBOL[s]
            m = _CCY_CODE_RX.search(s)
            if m:
                return m.group(1).upper()
            if len(s) <= 4:                 # already a short code-like token
                return s.upper()
    for k in _AMOUNT_FIELDS:                # sniff from the amount value itself
        v = f.get(k)
        if isinstance(v, str):
            for sym, code in _CCY_SYMBOL.items():
                if sym in v:
                    return code
            m = _CCY_CODE_RX.search(v)
            if m:
                return m.group(1).upper()
    return ""


def _fmt_amt(a: float, ccy: str) -> str:
    """'1,234.56 USD' / '1,234.56' when the currency is unknown."""
    return f"{a:,.2f}{(' ' + ccy) if ccy else ''}"


def _answer_money(db: Session, tenant_id: str, text: str) -> str | None:
    """Deterministic money aggregation ('combined total across invoices', 'do any documents mention
    money? how much in each') — sum/list amounts from money-bearing docs. The agent loops or gives
    up (INSUFFICIENT_EVIDENCE) on these; this is fast + correct. None → not a money question."""
    tl = (text or "").lower()
    # Fire ONLY for an explicit cross-doc SUM/TOTAL aggregate. Single-doc questions ("how much is my
    # BookMyShow ticket"), specific-type-without-aggregate ("total to pay on my credit card"), and
    # "do any mention money" all fall through to GROUNDED RAG — which reads the actual document and
    # cites it (that's how the correct credit-card total came out). A deterministic sum is only safer
    # than RAG when the ask is genuinely "add up many documents".
    wants = bool(re.search(
        r"(combined total|total (of|across)|sum of|added up|altogether|grand total|\bin total\b|"
        r"how much .*(in total|combined|altogether|across all|in all)|"
        r"total (spend|spent|paid)\b|how much did i (spend|pay)|"
        r"how much money.*(total|in all|altogether))", tl))
    # Beyond sum: average, extremum (largest/smallest), and threshold filters. These fire ONLY with an
    # explicit money signal — otherwise "more than 5 documents" or "the lowest floor in my lease" would
    # be hijacked into an amount table. A money signal = a currency symbol, or a money/type word.
    _money_ctx = bool(re.search(
        r"[$€£]|\b(amount|amounts|cost|costs|price|priced|pricing|spend|spent|paid|pay|payment|payments|"
        r"invoice|invoices|receipt|receipts|bill|bills|money|monetary|dollar|dollars|sgd|usd|eur|gbp|"
        r"total|totals|charge|charges|expense|expenses|owe|balance|fee|fees)\b", tl))
    _avg = _money_ctx and bool(re.search(r"\b(average|avg|mean|typical)\b", tl))
    _ext = _money_ctx and bool(re.search(r"\b(largest|biggest|highest|greatest|most expensive|maximum|"
                                         r"smallest|lowest|least expensive|cheapest|minimum)\b", tl))
    _thr = (re.search(r"\b(over|above|more than|greater than|exceeding|exceeds|exceed|at least|"
                      r"under|below|less than|at most)\s*\$?\s*([\d,]+(?:\.\d{1,2})?)", tl)
            if _money_ctx else None)
    if not (wants or _avg or _ext or _thr):
        return None
    uid = get_current_owner_user_pk()
    filters = [Document.tenant_id == tenant_id, Document.is_archived.is_(False),
               Document.ingestion_status == "ready", Document.doc_type.in_(_MONEY_TYPES)]
    if uid is not None:
        filters.append(Document.owner_user_id == uid)
    money_docs = db.scalars(select(Document).where(*filters)).all()
    # If the question names a specific money TYPE, restrict to it — and if that type has no
    # extractable amount, return None so RAG/agent reads that doc. NEVER answer a "credit card total"
    # (or "bank statement total") with an invoice list. This is the #1 trust-breaker to avoid.
    _TYPE_HINTS = {
        "invoice": ("invoic",),
        "receipt": ("receipt",),
        "credit_card_statement": ("credit card", "creditcard", "credit-card", "card statement",
                                  "card bill", "cc statement"),
        "customer_payment": ("customer payment",),
        "proforma_invoice": ("proforma",),
    }
    named = [t for t, hints in _TYPE_HINTS.items() if any(h in tl for h in hints)]
    # bank statement is named but excluded from money types (it's a balance) → don't answer here.
    if re.search(r"\bbank statement|bank account\b", tl):
        return None
    if named:
        money_docs = [d for d in money_docs if d.doc_type in named]
        if not money_docs:
            return None
    rows = [(d, _doc_amount(d)) for d in money_docs]
    priced = [(d, a) for d, a in rows if a is not None]
    if not priced:
        return None  # nothing parseable → let the agent/RAG read the actual doc(s)
    missing = len(money_docs) - len(priced)
    scope = (named[0].replace("_", " ") if len(named) == 1 else None)
    _scope_s = f" ({scope}s)" if scope else ""

    # Currency-aware: NEVER collapse different currencies into one figure. Group the
    # priced docs by their detected currency; a total/average is only reported as a
    # single number when everything is one currency, else it's split per-currency.
    ccy_of = {d.pk: _doc_currency(d) for d, _ in priced}
    by_ccy: dict[str, list] = defaultdict(list)
    for d, a in priced:
        by_ccy[ccy_of[d.pk]].append((d, a))
    multi_ccy = len(by_ccy) > 1

    if _thr:
        op = _thr.group(1)
        thr = float(_thr.group(2).replace(",", ""))
        hi = op in ("over", "above", "more than", "greater than", "exceed", "exceeds", "exceeding", "at least")
        inc = op in ("at least", "at most")
        sel = ([(d, a) for d, a in priced if (a >= thr if inc else a > thr)] if hi
               else [(d, a) for d, a in priced if (a <= thr if inc else a < thr)])
        if not sel:
            return f"No document has an amount {op} {thr:,.0f}. *(Checked {len(priced)} priced document(s).)*"
        out = ["| Document | Amount |", "|---|---|"] + [f"| {d.name} | {_fmt_amt(a, ccy_of[d.pk])} |"
                                                        for d, a in sorted(sel, key=lambda x: -x[1])]
        out += ["", f"**{len(sel)} document(s)** with an amount {op} {thr:,.0f}{_scope_s}."]
        if multi_ccy:
            out.append("*(Amounts span multiple currencies — the threshold is compared to each document's raw number.)*")
        return "\n".join(out)
    if _ext:
        want_min = bool(re.search(r"smallest|lowest|least|cheapest|minimum", tl))
        want_max = bool(re.search(r"largest|biggest|highest|greatest|most expensive|maximum", tl))
        out = []
        for ccy, items in sorted(by_ccy.items()):
            pfx = f"{ccy or 'unknown currency'} · " if multi_ccy else ""
            dmax = max(items, key=lambda x: x[1])
            dmin = min(items, key=lambda x: x[1])
            if want_max or not want_min:
                out.append(f"- **{pfx}Largest:** {dmax[0].name} — {_fmt_amt(dmax[1], ccy)}")
            if want_min:
                out.append(f"- **{pfx}Smallest:** {dmin[0].name} — {_fmt_amt(dmin[1], ccy)}")
        return f"Across {len(priced)} priced document(s){_scope_s}:\n" + "\n".join(out)
    if _avg:
        if multi_ccy:
            out = ["Amounts span multiple currencies — averages are kept separate:"]
            for ccy, items in sorted(by_ccy.items()):
                s = sum(a for _, a in items)
                out.append(f"- **{ccy or 'unknown currency'}: avg {s / len(items):,.2f}** "
                           f"across {len(items)} document(s) (total {s:,.2f})")
            return "\n".join(out)
        ccy = next(iter(by_ccy))
        total = sum(a for _, a in priced)
        return (f"**Average amount: {_fmt_amt(total / len(priced), ccy)}** across "
                f"{len(priced)} document(s){_scope_s} (total {_fmt_amt(total, ccy)}).")

    # default · combined total (SUM)
    lines = ["| Document | Amount |", "|---|---|"]
    lines += [f"| {d.name} | {_fmt_amt(a, ccy_of[d.pk])} |" for d, a in priced]
    note = (f" {missing} document(s) had no extractable amount and are excluded." if missing else "")
    if multi_ccy:
        subtotals = [f"- **{ccy or 'unknown currency'}: {sum(a for _, a in items):,.2f}** ({len(items)} document(s))"
                     for ccy, items in sorted(by_ccy.items())]
        lines += ["", f"Your documents span **{len(by_ccy)} currencies** — totals are kept separate "
                  f"(summing across currencies isn't meaningful):", *subtotals]
        if note:
            lines.append(f"*{note.strip()}*")
    else:
        ccy = next(iter(by_ccy))
        total = sum(a for _, a in priced)
        label = f"Total across your {scope}s" if scope else "Combined total"
        lines += ["", f"**{label}: {_fmt_amt(total, ccy)}** across {len(priced)} document(s).{note}"]
    return "\n".join(lines)


_META_FIELDS = {"description", "required", "detected_doc_type", "doc_type"}


def _compare_value(d, key: str):
    """(display, norm) for a field on a doc. Arrays → 'N items'; empties → '—'."""
    v = ((d.extracted_fields or {}).get("fields") or {}).get(key)
    if isinstance(v, list):
        return (f"{len(v)} item{'' if len(v) == 1 else 's'}", f"len:{len(v)}")
    if isinstance(v, dict):
        return ("{…}", "obj")
    if v in (None, "", []):
        return ("—", "")
    s = str(v).strip()
    return (s[:60] + ("…" if len(s) > 60 else ""), s.lower())


def _resolve_compare_docs(db: Session, filters: list, text: str) -> list | None:
    """Which docs to compare: a doc TYPE named in the question ('my two resumes') with ≥2 docs, or
    docs whose distinctive name tokens appear in the text ('compare EA07 and SA2021'). Cap at 4."""
    tl = (text or "").lower()
    all_docs = db.scalars(select(Document).where(*filters)).all()
    by_type: dict = {}
    for d in all_docs:
        by_type.setdefault(d.doc_type or "", []).append(d)
    for dt, docs in by_type.items():
        dtn = (dt or "").replace("_", " ")
        if dtn and len(docs) >= 2 and (dtn in tl or dtn.rstrip("s") in tl or (dtn + "s") in tl):
            return docs[:4]
    named = []
    for d in all_docs:
        toks = [t for t in re.split(r"[^a-z0-9]+", (d.name or "").lower()) if len(t) >= 4]
        if any(t in tl for t in toks):
            named.append(d)
    return named[:4] if len(named) >= 2 else None


def _answer_compare(db: Session, tenant_id: str, text: str) -> str | None:
    """Deterministic side-by-side comparison (the agent loops + hits MAX_STEPS on this). Leads with
    the KEY DIFFERENCES, then a table with 🟢/🔴 per row so differences pop. None → not a compare."""
    if not re.search(r"\b(compare|comparison|differe|differ|different|versus|vs\.?|side.?by.?side)\b",
                     (text or "").lower()):
        return None
    uid = get_current_owner_user_pk()
    filters = [Document.tenant_id == tenant_id, Document.is_archived.is_(False),
               Document.ingestion_status == "ready"]
    if uid is not None:
        filters.append(Document.owner_user_id == uid)
    docs = _resolve_compare_docs(db, filters, text)
    if not docs or len(docs) < 2:
        return None

    keys: list = []
    for d in docs:
        for k in ((d.extracted_fields or {}).get("fields") or {}):
            if k not in keys and k not in _META_FIELDS:
                keys.append(k)

    rows, diffs = [], []
    for k in keys:
        cells = [_compare_value(d, k) for d in docs]
        disp = [c[0] for c in cells]
        same = len({c[1] for c in cells}) == 1
        # a field only present (non-empty) on some docs is a difference worth showing
        if all(c[0] == "—" for c in cells):
            continue
        mark = "🟢" if same else "🔴"
        rows.append(f"| {k.replace('_', ' ')} | " + " | ".join(disp) + f" | {mark} |")
        if not same:
            diffs.append((k.replace("_", " "), disp))

    if not rows:
        return None
    head = "| Field | " + " | ".join(f"**{d.name[:22]}**" for d in docs) + " | |"
    sep = "|---|" + "|".join("---" for _ in docs) + "|:-:|"
    out = ["Comparing " + " vs. ".join(f"**{d.name}**" for d in docs) + ":", ""]
    if diffs:
        out.append(f"**Key differences ({len(diffs)}):**")
        out += [f"- **{k}** — " + " · ".join(disp) for k, disp in diffs[:8]]
        if len(diffs) > 8:
            out.append(f"- …and {len(diffs) - 8} more (see table)")
        out.append("")
    else:
        out += ["*These documents match on every extracted field.*", ""]
    out += [head, sep] + rows
    out += ["", "🟢 = same · 🔴 = differs"]
    return "\n".join(out)



_IDENTITY_INTENT = re.compile(
    r"(my (full |legal )?name\b|date of birth|\bdob\b|\bnationality\b|citizenship|"
    r"passport (number|no|details)|national id|\bnric\b|\bic (number|no)|id number|"
    r"what.{0,30}(passport|national id|nric).{0,20}(number|say|show|detail)|"
    r"full name.{0,30}(birth|nationality)|name.{0,20}birth)", re.I)
_ID_DOC_TYPES = ("national_id", "passport", "drivers_license", "residence_permit")
_ID_FIELD_LABELS = [
    ("Full name", ("full_name", "name", "holder_name", "given_name", "surname")),
    ("Date of birth", ("date_of_birth", "dob", "birth_date")),
    ("Nationality", ("nationality", "citizenship", "country_of_birth")),
    ("Document number", ("document_number", "passport_number", "id_number", "nric", "number")),
    ("Sex", ("sex", "gender")),
    ("Expiry", ("expiry_date", "date_of_expiry", "expiration_date")),
]


# ── Generic entity + type resolver ───────────────────────────────────────────
# "Rajesh Goda's national id", "invoices from Acme Corp", "documents about Singapore".
# Resolve ANY named graph entity (person/org/location/product) → its documents →
# optional doc-type filter → the values + source doc. ONE handler over the graph's
# `kind` column, not one per entity type. Flag: settings.entity_type_resolver.
_LOOKUP_STOP = {
    "show", "me", "my", "the", "a", "an", "of", "for", "is", "are", "what", "which", "list",
    "get", "find", "please", "give", "all", "his", "her", "its", "their", "tell", "about",
    "document", "documents", "doc", "docs", "file", "files", "details", "detail", "number",
    "no", "record", "records", "any", "do", "i", "have", "s", "and", "to", "in", "on", "with",
    "from", "by", "that", "this", "there", "whose", "who", "where",
}


def _detect_type_stems(tl: str):
    """→ (label, stems) for the first matching doc-type synonym in the query, else None."""
    for label, stems in _TYPE_SYNONYMS.items():
        if label in tl or any(s in tl for s in stems):
            return label, stems
    return None


_PROFILE_INTENT = re.compile(
    r"\b(everything about|profile of|who is|who's|what do you know about|all about|"
    r"tell me everything|tell me about)\b")


def _render_entity_profile(p: dict) -> str:
    """Render a resolved cross-document entity profile as a grounded markdown answer."""
    out = [f"**{p['name']}** — {p['kind']}, appears across **{p['docCount']}** of your documents."]
    if p.get("documents"):
        out.append("\n**Documents:** " + ", ".join(d["name"] for d in p["documents"][:10]))
    if p.get("related"):
        out.append("**Connected to:** " + ", ".join(
            f"{r['name']} ({r['sharedDocs']} shared)" for r in p["related"][:8]))
    if p.get("timeline"):
        out.append("**Dates seen:** " + ", ".join(t["value"] for t in p["timeline"][:8]))
    if p.get("identifiers"):
        out.append("**Identifiers:** " + ", ".join(i["value"] for i in p["identifiers"][:6]))
    if p.get("amounts"):
        out.append("**Amounts:** " + ", ".join(a["value"] for a in p["amounts"][:6]))
    return "\n".join(out)


def _answer_entity_profile(db: Session, tenant_id: str, text: str) -> str | None:
    """'Tell me everything about X / who is X / profile of X' → the cross-document
    entity intelligence profile (resolved identity + documents + network + timeline
    + identifiers/amounts). Rides the DOCAIQ_ENTITY_TYPE_RESOLVER flag. None → no
    profile intent or no matching entity."""
    from app.config import get_settings
    if not get_settings().entity_type_resolver:
        return None
    tl = (text or "").lower()
    if not _PROFILE_INTENT.search(tl):
        return None
    # residual = query minus the intent phrase + stopwords → the entity name to resolve
    residual = _PROFILE_INTENT.sub(" ", tl)
    toks = [t for t in re.split(r"[^a-z0-9]+", residual)
            if len(t) >= 2 and t not in _LOOKUP_STOP]
    name = " ".join(toks).strip()
    if not name:
        return None
    from app.services.entity_profile import build_profile
    prof = build_profile(db, name)
    if not prof or prof.get("docCount", 0) == 0:
        return None
    return _render_entity_profile(prof)


def _answer_entity_lookup(db: Session, tenant_id: str, text: str) -> str | None:
    """Resolve a named entity (any kind) from the query against the entities graph, narrow to
    that entity's documents, optionally filter by doc-type, and return the values + source docs.
    None → no known entity named in the query (fall through to the other handlers)."""
    from app.config import get_settings
    if not get_settings().entity_type_resolver:
        return None
    tl = (text or "").lower()
    type_hit = _detect_type_stems(tl)
    type_words: set[str] = set()
    if type_hit:
        type_words = {w for s in type_hit[1] for w in s.split()} | set(type_hit[0].split())
    # Need a lookup shape: a doc-type mention OR a possessive / preposition tying a name to a query.
    if not (type_hit or re.search(r"'s\b|s'\b|\b(of|for|from|about|by|belonging|owned by)\b", tl)):
        return None

    from app.orm import Entity
    uid = get_current_owner_user_pk()
    q = (select(Entity.canonical, Entity.text, Entity.kind, Entity.document_pk)
         .join(Document, Document.pk == Entity.document_pk)
         .where(Entity.tenant_id == tenant_id,
                Entity.kind.in_(("person", "org", "location", "product")),
                Document.is_archived.is_(False), Document.ingestion_status == "ready"))
    if uid is not None:
        q = q.where(Document.owner_user_id == uid)
    rows = db.execute(q).all()
    if not rows:
        return None

    by_name: dict[str, dict] = {}
    for canon, txt, kind, dpk in rows:
        nm = (canon or txt or "").strip().lower()
        if not nm:
            continue
        e = by_name.setdefault(nm, {"kind": kind, "docs": set(), "display": (txt or canon or nm).strip()})
        e["docs"].add(dpk)

    # Name tokens from the query, in order (clean header) + as a set (matching). Split on
    # any non-alphanumeric so a possessive like "goda's" yields "goda" (not "goda's").
    name_toks = [t for t in re.split(r"[^a-z0-9]+", tl)
                 if len(t) >= 2 and t not in _LOOKUP_STOP and t not in type_words]
    qtoks = set(name_toks)
    if not qtoks:
        return None

    # Match query name tokens against known entity names. A partial-name query is a SUBSET
    # of a fuller entity name ("kalyani" ⊆ "kalyani goda"), so accept a subset match as well
    # as a strong ≥2-token overlap (which disambiguates a shared surname like "goda"). The
    # same person is often FRAGMENTED across variants ("kalyani goda", "kalyani goda rajesh
    # -[NRIC]") — union the documents of every matching variant so nothing is missed.
    matched_docs: set = set()
    for nm, info in by_name.items():
        ntoks = {t for t in re.split(r"[^a-z0-9]+", nm) if len(t) >= 2 and t not in _LOOKUP_STOP}
        overlap = ntoks & qtoks
        if overlap and (overlap == qtoks or len(overlap) >= 2):
            matched_docs |= info["docs"]
    if not matched_docs:
        # A clearly-named lookup that resolves to nobody → a clean 'none found', never a
        # whole-library dump (the greedy overview no longer fires on '... of <name>').
        if not type_hit and any(t.isalpha() and len(t) >= 3 for t in qtoks):
            return (f"I don't find any documents associated with "
                    f"**{' '.join(t.title() for t in name_toks)}** in your workspace.")
        return None
    info = {"display": " ".join(t.title() for t in name_toks), "docs": matched_docs}

    docs = list(db.scalars(select(Document).where(Document.pk.in_(info["docs"]))).all())
    if type_hit:
        docs = [d for d in docs if d.doc_type and any(s in d.doc_type.lower() for s in type_hit[1])]
    if not docs:
        if type_hit:
            return f"I found **{info['display']}** in your documents, but none match “{type_hit[0]}”."
        return None
    # ENTERPRISE SEAM · a permission check would gate disclosure of another person's data here,
    # before rendering. Today per-user isolation already scopes `docs` to the owner's own set.
    docs = sorted(docs, key=lambda d: (d.doc_type or "", d.name or ""))[:12]
    header = f"**{info['display']}**" + (f" · {type_hit[0]}" if type_hit else "")
    blocks = []
    for d in docs:
        f = ((d.extracted_fields or {}).get("fields") or {})
        vals = []
        if (d.doc_type or "") in _ID_DOC_TYPES:
            for label, keys in _ID_FIELD_LABELS:
                for k in keys:
                    v = f.get(k)
                    if v not in (None, "", []) and not re.fullmatch(r"\[[A-Z_]+_\d+\]", str(v).strip()):
                        vals.append(f"  - {label}: **{str(v).strip()}**")
                        break
        dt = _doc_primary_date(d)
        src = (d.doc_type or "document").replace("_", " ").title() + f" · {d.name}" + (f" · {dt}" if dt else "")
        blocks.append(f"**{src}**" + ("\n" + "\n".join(vals) if vals else ""))
    return (f"{header} — {len(docs)} document{'s' if len(docs) != 1 else ''}:\n\n"
            + "\n\n".join(blocks) + "\n\n*(As extracted — verify against the source document.)*")


def _answer_identity(db: Session, tenant_id: str, text: str) -> str | None:
    """Specific identity fields ('what is my full name, date of birth and nationality') pulled straight
    from the owner's ID / passport extracted fields. Deterministic — the agent dead-ends on these.
    Returns to the OWNER their own data (redaction is only for the LLM boundary). None → not an ID Q."""
    if not _IDENTITY_INTENT.search(text or ""):
        return None
    uid = get_current_owner_user_pk()
    filters = [Document.tenant_id == tenant_id, Document.is_archived.is_(False),
               Document.ingestion_status == "ready", Document.doc_type.in_(_ID_DOC_TYPES)]
    if uid is not None:
        filters.append(Document.owner_user_id == uid)
    docs = db.scalars(select(Document).where(*filters)).all()
    if not docs:
        return None
    blocks = []
    for d in docs:
        f = ((d.extracted_fields or {}).get("fields") or {})
        vals = []
        for label, keys in _ID_FIELD_LABELS:
            for k in keys:
                v = f.get(k)
                if v not in (None, "", []) and not re.fullmatch(r"\[[A-Z_]+_\d+\]", str(v).strip()):
                    vals.append(f"- {label}: **{str(v).strip()}**")
                    break
        if vals:
            blocks.append(f"**{(d.doc_type or '').replace('_', ' ').title()}** — {d.name}\n" + "\n".join(vals))
    if not blocks:
        return None
    return ("From your identity documents:\n\n" + "\n\n".join(blocks)
            + "\n\n*(As extracted — verify against the original document.)*")


_POSSESS_TYPES = {
    "passport": ("passport",), "national_id": ("national id", "nric", "identity card", "ic number"),
    "invoice": ("invoice",), "receipt": ("receipt",), "bank_statement": ("bank statement", "bank account"),
    "credit_card_statement": ("credit card", "card statement", "card bill"),
    "master_service_agreement": ("contract", "agreement", " msa"),
    "training_certificate": ("certificate", "certification", "qualification"),
    "resume": ("resume", " cv "), "financial_report": ("financial report", "p&l", "balance sheet"),
    "passport_photo": (), "drivers_license": ("driver", "driving licence", "driving license"),
}


def _owner_docs(db: Session, tenant_id: str):
    uid = get_current_owner_user_pk()
    filters = [Document.tenant_id == tenant_id, Document.is_archived.is_(False),
               Document.ingestion_status == "ready"]
    if uid is not None:
        filters.append(Document.owner_user_id == uid)
    return db.scalars(select(Document).where(*filters)).all()


def _answer_possession(db: Session, tenant_id: str, text: str) -> str | None:
    """'Do I have a passport?' / 'do I have both a passport and a national ID?' — a document-existence
    check across the owner's types. None → not a possession question."""
    tl = (text or "").lower()
    if not re.search(r"\b(do i have|do i own|have i got|is there a|are there any|do i possess)\b", tl):
        return None
    asked = [t for t, hints in _POSSESS_TYPES.items() if any(h in tl for h in hints)]
    if not asked:
        return None
    from collections import Counter
    have = Counter(d.doc_type for d in _owner_docs(db, tenant_id))
    parts = []
    for t in asked:
        n = have.get(t, 0)
        label = t.replace("_", " ")
        parts.append(f"{'✅' if n else '❌'} **{label}** — {'yes, ' + str(n) + ' on file' if n else 'not found'}")
    lead = ("Yes — you have all of these." if all(have.get(t) for t in asked)
            else "You have some but not all of these." if any(have.get(t) for t in asked)
            else "No — none of these are on file.")
    return lead + "\n\n" + "\n".join(parts)


def _answer_currency(db: Session, tenant_id: str, text: str) -> str | None:
    """'What currencies appear?' / 'do I have documents in more than one currency?' — scan currency
    fields + amount symbols across the owner's documents. None → not a currency question."""
    tl = (text or "").lower()
    if not re.search(r"\bcurrenc", tl):
        return None
    found: dict[str, set] = {}
    for d in _owner_docs(db, tenant_id):
        f = ((d.extracted_fields or {}).get("fields") or {})
        for k, v in f.items():
            if v in (None, "", []):
                continue
            if "currency" in k.lower() and isinstance(v, str) and 2 <= len(v.strip()) <= 4:
                found.setdefault(v.strip().upper(), set()).add(d.name)
    if not found:
        return None
    codes = sorted(found)
    if len(codes) == 1:
        c = codes[0]
        return (f"All your documents use a **single currency: {c}** "
                f"({len(found[c])} document(s) specify it). No multi-currency exposure detected.")
    lines = [f"- **{c}** — {len(found[c])} document(s)" for c in codes]
    return f"Your documents span **{len(codes)} currencies**:\n\n" + "\n".join(lines)


def _answer_contract(db: Session, tenant_id: str, text: str) -> str | None:
    """'Key terms and duration of my contract' — pull structured fields from contract / MSA docs.
    None → not a contract question (or no contract on file)."""
    tl = (text or "").lower()
    if not re.search(r"\b(contract|agreement|\bmsa\b)\b", tl):
        return None
    if not re.search(r"\b(key term|terms|duration|period|expir|valid|parties|when.*(end|expire|start)|"
                     r"what.*(contract|agreement)|about my (contract|agreement)|summar)", tl):
        return None
    docs = [d for d in _owner_docs(db, tenant_id)
            if d.doc_type in ("master_service_agreement", "contract")]
    if not docs:
        return None
    out = []
    for d in docs:
        f = ((d.extracted_fields or {}).get("fields") or {})

        def g(*keys):
            for k in keys:
                v = f.get(k)
                if v not in (None, "", []):
                    return str(v).strip()
            return None
        rows = []
        title = g("title", "contract_id", "platform_name")
        if g("parties", "issuer"):
            rows.append(f"- Parties: {g('issuer', 'parties')[:90]}")
        start = g("effective_date", "primary_date")
        end = g("expiration_date", "contract_valid_until", "contract_period_end")
        if start or end:
            rows.append(f"- Duration: {start or '—'} → {end or '—'}")
        if g("services_included"):
            rows.append(f"- Services: {g('services_included')[:120]}")
        if g("company_registration_number"):
            rows.append(f"- Reg. no: {g('company_registration_number')}")
        head = f"**{title or (d.doc_type or 'Contract').replace('_', ' ').title()}** — {d.name}"
        out.append(head + ("\n" + "\n".join(rows) if rows else "\n- (no structured terms extracted)"))
    return "\n\n".join(out) + "\n\n*(As extracted — check the original for the binding text.)*"


def _answer_capability(db: Session, tenant_id: str, text: str) -> str | None:
    """'What questions can you NOT answer from my documents?' — a grounded capability + gap statement
    from the actual inventory, instead of a dead 'insufficient evidence'. None → not that question."""
    tl = (text or "").lower()
    if not re.search(r"(what.{0,30}(can(no|')?t? you|are you (un)?able)|"
                     r"question.{0,20}(can'?t|cannot|not able|unable)|"
                     r"what can you not|your (limitation|limits)|what do you not know)", tl):
        return None
    from collections import Counter
    types = Counter(d.doc_type or "unclassified" for d in _owner_docs(db, tenant_id))
    have = ", ".join(f"{t.replace('_', ' ')}" for t, _ in types.most_common(8))
    return ("I answer strictly from your documents, so I **can't** answer:\n\n"
            f"- Anything requiring a document you haven't added — I currently see: {have}. "
            "So no tax returns, proof of address, medical or lab reports, etc. unless you upload them.\n"
            "- Personal facts not written in any document (e.g. relationships, preferences, health).\n"
            "- Real-time or world knowledge (news, weather, prices) — I have no web access.\n\n"
            "I **can** answer about the content, amounts, dates, parties and cross-document patterns "
            f"of your **{sum(types.values())} documents**. Ask me about any of them.")


def _answer_profile(db: Session, tenant_id: str, text: str) -> str | None:
    """Grounded self-summary for open-ended 'what can you infer about me / who am I' — built from the
    document inventory (types + light inference) instead of a dead INSUFFICIENT_EVIDENCE."""
    if not re.search(r"\b(about me|about myself|who am i|infer.*me|know about me|describe me|"
                     r"tell me about me|profile me|my profile)\b|"
                     r"(summ(ary|ari[sz]e).{0,40}(document|file|everything|my (doc|file|record))|"
                     r"one.?paragraph|overall summary|story .*document|what .*document.*(tell|say) about)",
                     (text or "").lower()):
        return None
    from collections import Counter
    uid = get_current_owner_user_pk()
    filters = [Document.tenant_id == tenant_id, Document.is_archived.is_(False),
               Document.ingestion_status == "ready"]
    if uid is not None:
        filters.append(Document.owner_user_id == uid)
    docs = db.scalars(select(Document).where(*filters)).all()
    if not docs:
        return None
    types = Counter(d.doc_type or "unclassified" for d in docs)
    tstr = ", ".join(f"{c} {t.replace('_', ' ')}{'' if c == 1 else 's'}" for t, c in types.most_common(8))
    lines = ["Here's what your documents suggest *(inferred from your files — not certain)*:", "",
             f"- You have **{len(docs)} documents** across **{len(types)} types**: {tstr}."]
    hints = []
    if types.get("resume") or types.get("training_certificate"):
        hints.append("a professional profile (resumes / training certificates)")
    if types.get("invoice") or types.get("receipt") or types.get("customer_payment"):
        hints.append("business or purchase activity (invoices / receipts)")
    if types.get("national_id") or types.get("passport"):
        hints.append("identity / travel documents")
    if types.get("bank_statement") or types.get("credit_card_statement") or types.get("investment_portfolio_statement"):
        hints.append("financial accounts (statements)")
    if hints:
        lines.append("- Together these point to: " + "; ".join(hints) + ".")
    lines += ["", "Ask me about any specific document for details."]
    return "\n".join(lines)



def deterministic_answer(db: Session, tenant_id: str, text: str,
                         include_overview: bool = False,
                         skip_name_regex: bool = False) -> str | None:
    """The full deterministic handler chain — the aggregations RAG/agent handle unreliably (counts,
    money, entities, identity, watchlist, …). Returns a grounded markdown answer or None to fall
    through to RAG. Relies on the owner ContextVar being set (session, SSO, or an owner-scoped API key),
    so it is safe to reuse from the /v1/ask API and the MCP server, not just the in-app chat.

    `include_overview=True` also answers whole-inventory questions ('how many documents', 'summarize my
    documents') with the document overview. The in-app chat keeps that OFF here because it gates the
    overview on there being no open/specific document (a doc-scoped 'summarize' must summarize THAT doc);
    the API/MCP are always owner-wide, so they opt in.

    `skip_name_regex=True` skips the REGEX name-extraction handlers (`_answer_entity_lookup`,
    `_answer_entities`) — the in-app chat sets this because its LLM intent layer already extracted
    clean names + routed name-list/count asks via `answer_name_query`; leaving the regex handlers on
    would let them grab garbage (e.g. 'Kalyani Date Birth') out of a fact question. The API/MCP keep
    them (no intent layer there)."""
    r = (_answer_watchlist(db, tenant_id, text)
         or _answer_entity_profile(db, tenant_id, text)
         or (None if skip_name_regex else _answer_entity_lookup(db, tenant_id, text))
         or (None if skip_name_regex else _answer_entities(db, tenant_id, text))
         or _answer_possession(db, tenant_id, text)
         or _answer_identity(db, tenant_id, text)
         or _answer_currency(db, tenant_id, text)
         or _answer_contract(db, tenant_id, text)
         or _answer_capability(db, tenant_id, text)
         or _answer_count_or_dates(db, tenant_id, text)
         or _answer_money(db, tenant_id, text)
         or _answer_compare(db, tenant_id, text)
         or _answer_profile(db, tenant_id, text))
    if r:
        return r
    if include_overview and _is_broad_overview(text):
        try:
            return _aggregate_overview(db, tenant_id)
        except Exception:  # noqa: BLE001
            return None
    return None


