"""Chat-faithfulness eval corpus — capture + label + export.

Captures one case per AI chat answer given to a CONSENTED free-tier user (question,
answer, cited evidence, abstained flag), then attaches the human 👍/👎 label when the
user leaves ChatFeedback. Exports in a Ragas/R4-friendly shape so real usage can measure
& regression-test RAG faithfulness.

Reuses the exact consent gate as the extraction corpus (`eval_corpus.is_training_eligible`)
— paid chats are never captured. Superadmin-export only; answers/evidence may hold PII,
covered by the free-plan training consent.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_current_tenant
from app.orm import FaithfulnessCase

log = logging.getLogger("docaiq.faithfulness_corpus")


def capture_case(ctx, msg) -> bool:
    """Snapshot one AI answer as a faithfulness case, for consented free users only.
    `ctx` is the chat ChatContext (question=ctx.text); `msg` the persisted AI ChatMessage.
    Best-effort; caller wraps in a savepoint + commits."""
    from app.documents_scope import get_current_owner_user_pk
    from app.services import eval_corpus
    owner = get_current_owner_user_pk()
    if not eval_corpus.is_training_eligible(ctx.db, owner):
        return False
    if getattr(msg, "pk", None) is None or not (getattr(msg, "text", "") or "").strip():
        return False
    meta = getattr(msg, "meta", None) or ""
    abstained = "insufficient_evidence" in meta.lower()
    tid = get_current_tenant()
    exists = ctx.db.scalar(select(FaithfulnessCase).where(
        FaithfulnessCase.tenant_id == tid, FaithfulnessCase.message_pk == msg.pk))
    if exists is not None:
        return False
    ctx.db.add(FaithfulnessCase(
        tenant_id=tid, message_pk=msg.pk,
        doc_id_external=getattr(ctx, "doc_id_external", None), scope="doc",
        question=(ctx.text or "")[:8000], answer=(msg.text or "")[:8000],
        meta=meta[:64] or None, abstained=abstained,
        confidence=getattr(msg, "confidence", None),
        citations=(getattr(msg, "citations", None) or []),
    ))
    ctx.db.flush()
    return True


def attach_label(db: Session, message_pk: int, *, direction: str,
                 category: str | None = None, suggestion: str | None = None,
                 rating: int | None = None) -> None:
    """Attach the human 👍/👎 label to a case (called when ChatFeedback is recorded).
    A labeled case is `verified` — real ground-truth signal. No-op when there's no case
    (paid chat / not captured). Best-effort; caller commits."""
    row = db.scalar(select(FaithfulnessCase).where(
        FaithfulnessCase.tenant_id == get_current_tenant(),
        FaithfulnessCase.message_pk == message_pk))
    if row is None:
        return
    row.label = direction if direction in ("up", "down") else row.label
    if category:
        row.category = category[:16]
    if suggestion:
        row.suggestion = suggestion[:8000]
    if rating is not None:
        row.rating = int(rating)
    row.verified = True
    row.updated_at = datetime.now(timezone.utc)
    db.flush()


def _to_record(r: FaithfulnessCase) -> dict:
    # Ragas/R4 shape: contexts = the cited evidence quotes; ground_truth = the user's
    # suggested-correct answer (present only on 👎-with-suggestion cases).
    contexts = [c.get("quote") for c in (r.citations or [])
                if isinstance(c, dict) and c.get("quote")]
    return {
        "question": r.question,
        "answer": r.answer,
        "contexts": contexts,
        "groundTruth": r.suggestion,
        "abstained": bool(r.abstained),
        "answerPath": r.meta,
        "label": r.label,
        "category": r.category,
        "rating": r.rating,
        "verified": bool(r.verified),
        "docId": r.doc_id_external,
    }


def export_cases(db: Session, tenant_id: str, *, labeled_only: bool = False,
                 limit: int = 5000) -> list[dict]:
    """Faithfulness records for the eval harness (most-recent first). `labeled_only`
    restricts to human-labeled cases — the trustworthy subset."""
    stmt = select(FaithfulnessCase).where(FaithfulnessCase.tenant_id == tenant_id)
    if labeled_only:
        stmt = stmt.where(FaithfulnessCase.label.is_not(None))
    stmt = stmt.order_by(FaithfulnessCase.updated_at.desc()).limit(limit)
    return [_to_record(r) for r in db.scalars(stmt).all()]


def coverage(db: Session, tenant_id: str) -> dict:
    """Corpus size + labels + abstention/path breakdown."""
    def _n(*conds) -> int:
        return int(db.scalar(select(func.count()).select_from(FaithfulnessCase).where(
            FaithfulnessCase.tenant_id == tenant_id, *conds)) or 0)
    total = _n()
    thumbs_up = _n(FaithfulnessCase.label == "up")
    thumbs_down = _n(FaithfulnessCase.label == "down")
    abstained = _n(FaithfulnessCase.abstained.is_(True))
    rows = db.execute(
        select(FaithfulnessCase.meta, func.count())
        .where(FaithfulnessCase.tenant_id == tenant_id)
        .group_by(FaithfulnessCase.meta)).all()
    by_path = {(m or "unknown").split(" ")[0]: int(c) for m, c in rows}
    return {"total": total, "labeled": thumbs_up + thumbs_down,
            "thumbsUp": thumbs_up, "thumbsDown": thumbs_down,
            "abstained": abstained, "byAnswerPath": by_path}
