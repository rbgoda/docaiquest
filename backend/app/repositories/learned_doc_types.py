"""M46 · learned_doc_types repository — the self-learning classification memory.

Per-user (documents product) open-vocabulary doc types the reconciler derives
from a doc's AI summary. Tenant + owner scoped.
"""
from __future__ import annotations

from sqlalchemy import select, desc
from sqlalchemy.orm import Session

from app.db import get_current_tenant
from app.documents_scope import get_current_owner_user_pk
from app.orm import LearnedDocType


def register(db: Session, type_slug: str, label: str | None, source: str = "ai") -> None:
    """Upsert a learned type for the current owner, bumping seen_count."""
    tid = get_current_tenant()
    uid = get_current_owner_user_pk()
    row = db.scalar(select(LearnedDocType).where(
        LearnedDocType.tenant_id == tid,
        LearnedDocType.owner_user_id == uid,
        LearnedDocType.type_slug == type_slug,
    ))
    if row is None:
        db.add(LearnedDocType(
            tenant_id=tid, owner_user_id=uid, type_slug=type_slug,
            label=label, source=source, seen_count=1,
        ))
    else:
        row.seen_count = (row.seen_count or 0) + 1
        if label and not row.label:
            row.label = label
        # A human correction upgrades the source (top priority); never downgrade.
        if source == "human":
            row.source = "human"
    db.flush()


def update_centroid(db: Session, type_slug: str, embedding: list[float]) -> None:
    """§2 · fold a doc's embedding into the type's running-mean centroid
    (owner-scoped). Incremental mean: c' = (c*n + e) / (n+1)."""
    tid = get_current_tenant()
    uid = get_current_owner_user_pk()
    row = db.scalar(select(LearnedDocType).where(
        LearnedDocType.tenant_id == tid,
        LearnedDocType.owner_user_id == uid,
        LearnedDocType.type_slug == type_slug,
    ))
    if row is None or not embedding:
        return
    n = row.centroid_n or 0
    if n == 0 or row.centroid is None:
        row.centroid = list(embedding)
    else:
        c = list(row.centroid)
        row.centroid = [(c[i] * n + embedding[i]) / (n + 1) for i in range(len(embedding))]
    row.centroid_n = n + 1
    db.flush()


def match_centroid(db: Session, embedding: list[float], threshold: float) -> tuple[str, str, float] | None:
    """§2 · nearest learned-type centroid to `embedding` (owner-scoped, cosine).
    Returns (slug, label, similarity) when similarity >= threshold, else None."""
    tid = get_current_tenant()
    uid = get_current_owner_user_pk()
    if uid is None or not embedding:
        return None
    from sqlalchemy import text as _sql_text
    vec = "[" + ",".join(f"{v:.6f}" for v in embedding) + "]"
    row = db.execute(_sql_text("""
        SELECT type_slug, label, 1 - (centroid <=> :qv) AS sim
          FROM learned_doc_types
         WHERE tenant_id = :tid AND owner_user_id = :uid AND centroid IS NOT NULL
         ORDER BY centroid <=> :qv ASC
         LIMIT 1
    """), {"qv": vec, "tid": tid, "uid": uid}).first()
    if row is None or row.sim is None or float(row.sim) < threshold:
        return None
    return (row.type_slug, row.label or row.type_slug, float(row.sim))


def list_all(db: Session) -> list[dict]:
    """Owner-scoped list of learned types, most-seen first."""
    tid = get_current_tenant()
    uid = get_current_owner_user_pk()
    base = [LearnedDocType.tenant_id == tid]
    if uid is not None:
        base.append(LearnedDocType.owner_user_id == uid)
    rows = db.scalars(
        select(LearnedDocType).where(*base).order_by(desc(LearnedDocType.seen_count))
    ).all()
    return [{"slug": r.type_slug, "label": r.label, "source": r.source,
             "seenCount": r.seen_count,
             # §2 · distilled = the type has a centroid, so similar docs now
             # auto-classify with no LLM call.
             "distilled": bool(r.centroid_n and r.centroid_n > 0)} for r in rows]
