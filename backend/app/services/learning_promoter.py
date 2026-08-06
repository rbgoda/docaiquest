"""M44.P10 PR2 · Promotion engine for delete-with-learning preservation.

Phase 1 of the two-phase delete. When a document is about to be
deleted, this engine walks its EVIDENCE rows (reflexion pairs, field edits,
agent traces, entities) and promotes the *generalizable* patterns up into the
tenant-level UNDERSTANDING tables so the knowledge survives the cascade that
Phase 2 will run:

    reflexion_pairs (doc-specific) → reflexion_pairs (general, doc_id NULL)
    field_edits (repeated across docs of a type) → extraction_corrections
    successful agent traces → agent_skill_memory
    person/org entities → entity_canonical

The conceptual rule (see the design doc): anything that mentions specific PII
or specific doc identity stays EVIDENCE (purged); anything that is a
generalizable *pattern* becomes UNDERSTANDING (preserved).

Flag status: nothing in the request path calls this until **P10 PR3** flips
``DOCAIQ_DELETE_WITH_LEARNING`` on. PR2 ships the engine here, unit-tested in
isolation, with zero behavior change.

Transaction contract: this function is **transaction-neutral — it never
commits**. The caller owns the transaction so Phase 1 stays "all-promotes-or-
nothing" in a single unit of work that also holds the ``SELECT FOR UPDATE`` on
the document row (PR3 wires that part). Tests commit explicitly.
"""

from __future__ import annotations

import re

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.orm import (
    AgentSkillMemory,
    AgentTrace,
    ChatMessage,
    Document,
    Entity,
    EntityCanonical,
    ExtractionCorrection,
    FieldEdit,
    ReflexionPair,
)

# ── Tunable thresholds (documented defaults from DELETE_WITH_LEARNING.md) ──
# Q1 in the design doc's "Open design questions": PR2 uses >= 2 as the
# starting bar for promoting a reflexion pair. Retune before flipping the
# flag on (PR3) — it lives here, not scattered, so that's a one-line change.
HELPFUL_MIN = 2
# Step 4: a field-extraction mistake only generalizes once we've seen the same
# field corrected on this many distinct documents of the same doc_type.
FIELD_EDIT_DOC_MIN = 3


# ── Pure helpers (no DB · unit-tested directly) ───────────────────────────

# Identifier shapes that mark a question as doc-specific → NOT promotable.
# Bias is intentionally toward over-detection: a false positive just means we
# keep one extra row as evidence (purged on delete), which is the safe miss.
_IDENT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),          # emails
    re.compile(r"[$£€]\s?\d"),                         # money amounts
    re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),              # ISO dates
    re.compile(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b"),  # dd/mm/yyyy dates
    re.compile(r"\b[A-Z]{2,}-?\d+\b"),                 # control IDs: AC-2, INV-123, ISO27001
    re.compile(r"\b\d{4,}\b"),                         # long digit runs: policy/account/invoice #
)

_SENTENCE_SPLIT = re.compile(r"(?<=[.?!])\s+")
_MULTISPACE = re.compile(r"\s+")
# Capitalized words that are normal at any position and must NOT count as
# proper nouns (interrogatives, articles, common verbs).
_CAPS_ALLOWED = {
    "I", "The", "A", "An", "What", "Is", "Are", "Was", "Were", "Does", "Do",
    "Did", "How", "Why", "When", "Which", "Who", "Whom", "Where", "Can",
    "Could", "Should", "Would", "Will", "This", "That", "These", "Those",
    "It", "Its",
}


def has_doc_specific_identifier(question: str) -> bool:
    """True when the question references something tied to one document —
    a number, email, money figure, date, control ID, or a proper noun. Such
    questions stay evidence; only generic ones ("what is the expiry date?")
    get promoted."""
    if not question:
        return False
    for pat in _IDENT_PATTERNS:
        if pat.search(question):
            return True
    # Proper-noun heuristic: a capitalized alphabetic word that is neither
    # sentence-initial nor an allowed interrogative/article.
    for sentence in _SENTENCE_SPLIT.split(question.strip()):
        for i, raw in enumerate(sentence.split()):
            w = raw.strip(".,;:?!\"'()[]{}")
            if i == 0 or not w or not w.isalpha():
                continue
            if w[0].isupper() and w not in _CAPS_ALLOWED:
                return True
    return False


def anonymize_question(question: str) -> str:
    """Replace doc-specific identifiers with typed placeholders. Belt-and-
    suspenders: questions reach here only after passing
    ``has_doc_specific_identifier``, so this is usually a near no-op, but it
    scrubs anything the proper-noun heuristic let through (e.g. a bare ID
    that slipped the gate)."""
    q = re.sub(r"[\w.+-]+@[\w-]+\.[\w.-]+", "{email}", question)
    q = re.sub(r"[$£€]\s?\d[\d,]*(?:\.\d+)?", "{amount}", q)
    q = re.sub(r"\b\d{4}-\d{2}-\d{2}\b", "{date}", q)
    q = re.sub(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b", "{date}", q)
    q = re.sub(r"\b\d{4,}\b", "{number}", q)
    return _MULTISPACE.sub(" ", q).strip()


def question_template(question: str) -> str:
    """Anonymized, lowercased form used as the dedup key for
    ``agent_skill_memory`` ("what is the {id_field}?")."""
    return anonymize_question(question).lower()


def is_successful_trace(steps: list[AgentTrace]) -> bool:
    """A ReAct trace counts as a reusable skill when it terminates in
    ``final_answer`` with no errored step along the way."""
    if not steps:
        return False
    if any(s.error for s in steps):
        return False
    return steps[-1].action_name == "final_answer"


def tool_sequence(steps: list[AgentTrace]) -> list[str]:
    """Ordered list of action names — the reusable tool recipe."""
    return [s.action_name for s in steps if s.action_name]


# ── DB orchestration (Phase 1) ────────────────────────────────────────────

def promote_doc_learnings(db: Session, doc_pk: int, *, mark_pending: bool = True, lock: bool = False) -> dict:
    """Run Phase 1 promotion for one document. Does NOT commit — the caller
    owns the transaction. Returns a telemetry summary (anticipates Q4 in the
    design doc: ``promoted_count`` per delete).

    ``lock=True`` takes a ``SELECT ... FOR UPDATE`` on the document row so a
    concurrent matcher / curator / agent touching the same doc blocks until
    the deleting transaction commits (the design's row-level lock guard rail).
    """
    doc = db.get(Document, doc_pk, with_for_update=True) if lock else db.get(Document, doc_pk)
    if doc is None:
        raise ValueError(f"document pk={doc_pk} not found")

    tenant_id = doc.tenant_id
    doc_type = doc.doc_type or "unknown"

    if mark_pending:
        doc.deletion_status = "pending"
        db.flush()

    summary = {
        "doc_pk": doc_pk,
        "doc_type": doc_type,
        "reflexions_promoted": _promote_reflexions(db, tenant_id, doc.id_external),
        "corrections_upserted": _promote_field_edits(db, tenant_id, doc_type, doc_pk),
        "skills_upserted": _promote_agent_skills(db, tenant_id, doc_type, doc.id_external),
        "canonicals_upserted": _promote_entities(db, tenant_id, doc_pk),
    }
    return summary


def _promote_reflexions(db: Session, tenant_id: str, doc_id_external: str) -> int:
    """Step 3 · promote helpful, identifier-free reflexion pairs from
    doc_specific → general (doc_id_external NULL, question anonymized)."""
    rows = db.scalars(
        select(ReflexionPair).where(
            ReflexionPair.tenant_id == tenant_id,
            ReflexionPair.doc_id_external == doc_id_external,
            ReflexionPair.kind == "doc_specific",
        )
    ).all()
    promoted = 0
    for r in rows:
        if r.helpful_count < HELPFUL_MIN:
            continue
        if r.helpful_count <= r.marked_unhelpful_count:
            continue  # contested — leave as evidence
        if has_doc_specific_identifier(r.question):
            continue
        r.question = anonymize_question(r.question)
        r.doc_id_external = None
        r.kind = "general"
        promoted += 1
    if promoted:
        db.flush()
    return promoted


def _promote_field_edits(db: Session, tenant_id: str, doc_type: str, doc_pk: int) -> int:
    """Step 4 · for each field corrected on this doc, if the same field has
    been corrected across FIELD_EDIT_DOC_MIN+ distinct docs of the same
    doc_type, upsert a ``frequent_mismatch`` extraction_correction."""
    paths = db.scalars(
        select(FieldEdit.field_path)
        .where(FieldEdit.tenant_id == tenant_id, FieldEdit.document_pk == doc_pk)
        .distinct()
    ).all()
    upserts = 0
    for path in paths:
        doc_count = db.scalar(
            select(func.count(func.distinct(FieldEdit.document_pk)))
            .select_from(FieldEdit)
            .join(Document, Document.pk == FieldEdit.document_pk)
            .where(
                FieldEdit.tenant_id == tenant_id,
                FieldEdit.field_path == path,
                Document.doc_type == doc_type,
            )
        ) or 0
        if doc_count >= FIELD_EDIT_DOC_MIN:
            _upsert_correction(db, tenant_id, doc_type, path, doc_count)
            upserts += 1
    return upserts


def _upsert_correction(db: Session, tenant_id: str, doc_type: str, field_path: str, observations: int) -> None:
    existing = db.scalar(
        select(ExtractionCorrection).where(
            ExtractionCorrection.tenant_id == tenant_id,
            ExtractionCorrection.doc_type == doc_type,
            ExtractionCorrection.pattern_kind == "frequent_mismatch",
            ExtractionCorrection.pattern["wrong_field"].astext == field_path,
        )
    )
    if existing is not None:
        existing.observations_count = observations
        existing.last_seen_at = func.now()
    else:
        db.add(
            ExtractionCorrection(
                tenant_id=tenant_id,
                doc_type=doc_type,
                pattern_kind="frequent_mismatch",
                pattern={"wrong_field": field_path},
                observations_count=observations,
                source="local",
            )
        )
    db.flush()


def _promote_agent_skills(db: Session, tenant_id: str, doc_type: str, doc_id_external: str) -> int:
    """Step 5 · for each successful agent trace answering a question on this
    doc, upsert its tool sequence into agent_skill_memory keyed by
    (doc_type, anonymized question template)."""
    ai_msg_pks = db.scalars(
        select(ChatMessage.pk).where(
            ChatMessage.tenant_id == tenant_id,
            ChatMessage.doc_id_external == doc_id_external,
            ChatMessage.role == "ai",
        )
    ).all()
    upserts = 0
    for msg_pk in ai_msg_pks:
        steps = db.scalars(
            select(AgentTrace)
            .where(AgentTrace.tenant_id == tenant_id, AgentTrace.chat_message_pk == msg_pk)
            .order_by(AgentTrace.step_index)
        ).all()
        if not is_successful_trace(steps):
            continue
        seq = tool_sequence(steps)
        if not seq:
            continue
        template = question_template(_question_for_ai_message(db, tenant_id, doc_id_external, msg_pk))
        if not template:
            continue
        _upsert_skill(db, tenant_id, doc_type, template, seq)
        upserts += 1
    return upserts


def _question_for_ai_message(db: Session, tenant_id: str, doc_id_external: str, ai_msg_pk: int) -> str:
    """The user question that prompted an AI answer = the most recent user
    message before it in the same single-doc conversation."""
    return db.scalar(
        select(ChatMessage.text)
        .where(
            ChatMessage.tenant_id == tenant_id,
            ChatMessage.doc_id_external == doc_id_external,
            ChatMessage.role == "user",
            ChatMessage.pk < ai_msg_pk,
        )
        .order_by(ChatMessage.pk.desc())
        .limit(1)
    ) or ""


def _upsert_skill(db: Session, tenant_id: str, doc_type: str, template: str, seq: list[str]) -> None:
    existing = db.scalar(
        select(AgentSkillMemory).where(
            AgentSkillMemory.tenant_id == tenant_id,
            AgentSkillMemory.doc_type == doc_type,
            AgentSkillMemory.question_template == template,
        )
    )
    if existing is not None:
        existing.success_count = existing.success_count + 1
        existing.tool_sequence = seq
        existing.last_used_at = func.now()
    else:
        db.add(
            AgentSkillMemory(
                tenant_id=tenant_id,
                doc_type=doc_type,
                question_template=template,
                tool_sequence=seq,
                success_count=1,
                source="local",
            )
        )
    db.flush()


def _promote_entities(db: Session, tenant_id: str, doc_pk: int) -> int:
    """Step 6 · upsert person/org entities on this doc into entity_canonical,
    merging alias spellings. LOCAL ONLY — never leaves the tenant (no
    `source` column on this table)."""
    rows = db.scalars(
        select(Entity).where(
            Entity.tenant_id == tenant_id,
            Entity.document_pk == doc_pk,
            Entity.kind.in_(("person", "org")),
        )
    ).all()

    # Group by (kind, canonical display form); collect distinct alias texts.
    grouped: dict[tuple[str, str], set[str]] = {}
    for e in rows:
        canonical = (e.canonical or e.text or "").strip()[:256]
        if not canonical:
            continue
        bucket = grouped.setdefault((e.kind, canonical), set())
        alias = (e.text or "").strip()[:256]
        if alias and alias.casefold() != canonical.casefold():
            bucket.add(alias)

    upserts = 0
    for (kind, canonical), aliases in grouped.items():
        _upsert_canonical(db, tenant_id, kind, canonical, aliases)
        upserts += 1
    return upserts


def _upsert_canonical(db: Session, tenant_id: str, kind: str, canonical: str, new_aliases: set[str]) -> None:
    existing = db.scalar(
        select(EntityCanonical).where(
            EntityCanonical.tenant_id == tenant_id,
            EntityCanonical.kind == kind,
            EntityCanonical.canonical == canonical,
        )
    )
    if existing is not None:
        merged = list(dict.fromkeys([*(existing.aliases or []), *sorted(new_aliases)]))
        existing.aliases = merged
        existing.observed_count = existing.observed_count + 1
    else:
        db.add(
            EntityCanonical(
                tenant_id=tenant_id,
                kind=kind,
                canonical=canonical,
                aliases=sorted(new_aliases),
                observed_count=1,
            )
        )
    db.flush()
