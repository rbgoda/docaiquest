"""M46 · type reconciler (self-learning classification, Phase 1).

When the closed-enum classifier returns 'other'/low-confidence, the document's
own AI summary usually already names the real type (e.g. "laboratory test
report"). This service feeds that understanding back: derive an open-vocabulary
slug from the summary, AUTO-assign it as doc_type, and learn it — recording the
extraction schema under the RIGHT type and registering the type in the per-user
learned-types registry (which powers the cross-doc 'apply to similar'
suggestion). Owner scope must be set by the caller.
"""
from __future__ import annotations

import logging
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.orm import ChatMessage
from app.repositories import learned_doc_types as ldt_repo
from app.repositories import learned_schemas as ls_repo
from app.services.doc_chat import doc_text_excerpt, llm_one_shot

log = logging.getLogger("docaiq.type_reconciler")

# Types we treat as "not really classified" and try to improve.
_WEAK = {None, "", "other", "unknown", "document", "misc", "general"}

_SYS = "You categorize documents. Reply with ONLY a short snake_case document-type label, nothing else."
_USR = (
    "From the description below, give the document's GENERAL TYPE/category as a "
    "short snake_case slug (1-2 words) — the KIND of document, NOT its specific "
    "subject, so that similar documents share one type. For example a blood test, "
    "an HIV test and a bone-density report are all 'lab_report' (NOT "
    "'hiv_test_report'). Prefer broad reusable categories: lab_report, "
    "medical_report, prescription, discharge_summary, vaccination_record, "
    "bank_statement, invoice, receipt, lease_agreement, insurance_policy, "
    "passport, utility_bill, tax_document. "
    "Output ONLY the slug — no prose, no punctuation except underscores.\n\n"
    "Description:\n{text}"
)


def _slugify(s: str) -> str | None:
    s = (s or "").strip().lower()
    s = s.split("\n")[0].strip().strip("`\"'. ")
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    if not s or len(s) > 48 or s in _WEAK:
        return None
    return s


def _summary_or_excerpt(db: Session, doc) -> str:
    """Prefer the AI summary (it already names the type); fall back to a text
    excerpt if no summary has been generated yet."""
    msg = db.scalar(select(ChatMessage).where(
        ChatMessage.tenant_id == doc.tenant_id,
        ChatMessage.doc_id_external == doc.id_external,
        ChatMessage.meta == "summary",
    ))
    if msg and msg.text:
        return msg.text[:2000]
    try:
        return doc_text_excerpt(db, doc.pk, max_chars=4000) or ""
    except Exception:  # noqa: BLE001
        return ""


def _doc_embedding(db: Session, text: str) -> list[float] | None:
    """Embed the doc's summary/excerpt — the same signal that determines its
    type — so centroids and queries live in one space. None on failure."""
    try:
        from app.embeddings import embed
        v = embed([text[:4000]])
        return list(v[0]) if v else None
    except Exception as e:  # noqa: BLE001
        log.debug("type_reconciler: embed failed: %s", e)
        return None


def _assign(db: Session, doc, slug: str, *, confidence: float, emb: list[float] | None) -> str:
    """Assign + learn a type, fold the doc into the type's centroid, commit.
    Shared by the cheap (distilled) and LLM paths."""
    label = slug.replace("_", " ").title()
    doc.doc_type = slug
    doc.doc_type_confidence = confidence
    doc.doc_type_alternatives = []
    try:
        # Record the DISTINCTIVE field labels (labeled-array labels), NOT the top-level
        # extracted_fields keys — those are meta (doc_type/chunk_refs/extracted_at/…) and
        # would pollute the learned vocabulary + crystallization. Mirrors
        # fact_extractor._learned_labels. (Bug found via the comprehensive local test.)
        ef = doc.extracted_fields if isinstance(doc.extracted_fields, dict) else {}
        fld = ef.get("fields") if isinstance(ef.get("fields"), dict) else {}
        labels: list[str] = []
        for arr in ("key_facts", "identifiers", "dates", "amounts"):
            labels += [i.get("label") for i in (fld.get(arr) or [])
                       if isinstance(i, dict) and i.get("label")]
        ls_repo.record(db, slug, labels, [])
    except Exception:  # noqa: BLE001 — learning is best-effort
        pass
    try:
        ldt_repo.register(db, slug, label, source="ai")
        if emb is not None:
            ldt_repo.update_centroid(db, slug, emb)
    except Exception:  # noqa: BLE001
        pass
    db.commit()
    return slug


def learn_human_type(db: Session, doc, raw_type: str) -> None:
    """§2 · HITL feed. A human set this doc's type — register it as a
    human-sourced learned type (top priority) and fold the doc into that type's
    centroid so similar future docs distill straight to it. Documents only;
    best-effort. Caller has owner scope set."""
    from app.config import get_settings
    if get_settings().product != "documents":
        return
    slug = _slugify(raw_type)
    if slug is None:
        return
    try:
        ldt_repo.register(db, slug, slug.replace("_", " ").title(), source="human")
        text = _summary_or_excerpt(db, doc)
        if len(text) >= 30:
            emb = _doc_embedding(db, text)
            if emb is not None:
                ldt_repo.update_centroid(db, slug, emb)
    except Exception as e:  # noqa: BLE001
        log.debug("learn_human_type failed for doc pk=%s: %s", getattr(doc, "pk", "?"), e)


def _cosine(a: list[float], b: list[float]) -> float:
    import math
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def find_candidates(db: Session, slug: str) -> list[dict]:
    """A3 · weakly-typed ('other') docs whose embedding is near `slug`'s centroid
    — i.e. docs that LOOK like this learned type. Owner-scoped suggestion list."""
    from app.config import get_settings
    from app.db import get_current_tenant
    from app.documents_scope import get_current_owner_user_pk
    from app.orm import LearnedDocType
    from app.repositories import documents as doc_repo
    row = db.scalar(select(LearnedDocType).where(
        LearnedDocType.tenant_id == get_current_tenant(),
        LearnedDocType.owner_user_id == get_current_owner_user_pk(),
        LearnedDocType.type_slug == slug,
    ))
    if row is None or row.centroid is None:
        return []
    centroid = list(row.centroid)
    thr = get_settings().centroid_match_threshold
    out: list[dict] = []
    for doc in doc_repo.list_unclassified(db):
        text = _summary_or_excerpt(db, doc)
        if len(text) < 30:
            continue
        emb = _doc_embedding(db, text)
        if emb is None:
            continue
        sim = float(_cosine(emb, centroid))
        if sim >= thr:
            out.append({"docId": doc.id_external, "name": doc.name, "similarity": round(sim, 3)})
    out.sort(key=lambda c: c["similarity"], reverse=True)
    return out


def apply_type_to_docs(db: Session, slug: str, doc_ids: list[str]) -> list[str]:
    """A3 · assign learned type `slug` to the given weak docs (owner-scoped).
    Skips docs that are already confidently/human-typed."""
    from app.repositories import documents as doc_repo
    applied: list[str] = []
    for did in doc_ids:
        doc = doc_repo.get_row(db, did)  # owner-scoped
        if doc is None:
            continue
        if doc.doc_type not in _WEAK and (doc.doc_type_confidence or 0) >= 0.5:
            continue
        text = _summary_or_excerpt(db, doc)
        emb = _doc_embedding(db, text) if len(text) >= 30 else None
        _assign(db, doc, slug, confidence=0.7, emb=emb)
        applied.append(did)
    return applied


def reconcile_doc(db: Session, doc) -> str | None:
    """If `doc` is weakly typed ('other'/low-confidence and not human-verified),
    assign + learn a better type. §2: first try a cheap centroid match against
    the user's already-learned types (NO LLM); only call the LLM when nothing
    matches. Returns the new slug, or None if unchanged. Caller sets owner scope."""
    # Never override a confident or human-verified classification.
    if doc.doc_type not in _WEAK and (doc.doc_type_confidence or 0) >= 0.5:
        return None
    text = _summary_or_excerpt(db, doc)
    if len(text) < 30:
        return None

    emb = _doc_embedding(db, text)

    # §2 · Distilled path — match against learned-type centroids, no LLM call.
    if emb is not None:
        from app.config import get_settings
        try:
            match = ldt_repo.match_centroid(db, emb, get_settings().centroid_match_threshold)
        except Exception as e:  # noqa: BLE001
            match = None
            log.debug("centroid match failed: %s", e)
        if match is not None:
            slug, _label, sim = match
            _assign(db, doc, slug, confidence=0.7, emb=emb)
            log.info("type_reconciler: doc pk=%s distilled → %r (centroid sim=%.2f · no LLM)",
                     doc.pk, slug, sim)
            return slug

    # Fallback · derive a fresh type from the summary via one LLM call.
    try:
        raw = llm_one_shot(db, _SYS, _USR.format(text=text), max_tokens=20)
    except Exception as e:  # noqa: BLE001
        log.warning("type_reconciler: LLM call failed for doc pk=%s: %s", doc.pk, e)
        return None
    slug = _slugify(raw)
    if slug is None:
        return None
    # Flag-gated: fold the LLM's free-form slug onto its canonical type when it's a
    # known synonym ('laboratory_test_report' → 'lab_report'), so classifications
    # converge instead of fragmenting. A slug with no alias is left as-is (open vocab).
    from app.config import get_settings
    if get_settings().type_canonicalize:
        from app.agents.classifier import canonicalize_doc_type
        canon = canonicalize_doc_type(slug)
        if canon and canon != slug:
            log.info("type_reconciler: canonicalized %r → %r", slug, canon)
            slug = canon
    _assign(db, doc, slug, confidence=0.75, emb=emb)
    log.info("type_reconciler: doc pk=%s reconciled 'other' → %r (LLM)", doc.pk, slug)
    return slug
