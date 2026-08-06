"""User document annotations (M53) — owner-scoped CRUD + markdown export.

Every query is scoped to the current tenant + owner (per-user isolation), so a
user can only see/edit/delete their own highlights, only on their own docs."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_current_tenant
from app.documents_scope import get_current_owner_user_pk
from app.orm import Document, DocumentAnnotation


def resolve_doc(db: Session, id_external: str) -> Document | None:
    """The document by id_external, owner-scoped."""
    tid = get_current_tenant()
    uid = get_current_owner_user_pk()
    stmt = select(Document).where(Document.tenant_id == tid, Document.id_external == id_external)
    if uid is not None:
        stmt = stmt.where(Document.owner_user_id == uid)
    return db.scalar(stmt)


def _to_dict(a: DocumentAnnotation) -> dict:
    return {
        "id": a.pk,
        "page": a.page,
        "bbox": [a.x0, a.y0, a.x1, a.y1],   # normalized 0..1
        "text": a.captured_text or "",
        "note": a.note or "",
        "color": a.color or "yellow",
        "createdAt": a.created_at.isoformat() if a.created_at else None,
    }


def list_for_doc(db: Session, id_external: str) -> list[dict] | None:
    d = resolve_doc(db, id_external)
    if d is None:
        return None
    tid = get_current_tenant()
    uid = get_current_owner_user_pk()
    stmt = select(DocumentAnnotation).where(
        DocumentAnnotation.tenant_id == tid, DocumentAnnotation.document_pk == d.pk)
    if uid is not None:
        stmt = stmt.where(DocumentAnnotation.owner_user_id == uid)
    rows = db.scalars(stmt.order_by(DocumentAnnotation.page, DocumentAnnotation.pk)).all()
    return [_to_dict(a) for a in rows]


def create(db: Session, id_external: str, *, page: int, x0: float, y0: float, x1: float, y1: float,
           captured_text: str | None, note: str | None = None, color: str | None = None) -> dict | None:
    d = resolve_doc(db, id_external)
    if d is None:
        return None
    a = DocumentAnnotation(
        tenant_id=get_current_tenant(), owner_user_id=get_current_owner_user_pk(),
        document_pk=d.pk, page=page, x0=x0, y0=y0, x1=x1, y1=y1,
        captured_text=captured_text, note=note, color=color or "yellow", source="user")
    db.add(a)
    db.flush()
    db.refresh(a)
    return _to_dict(a)


def _get_owned(db: Session, ann_id: int) -> DocumentAnnotation | None:
    tid = get_current_tenant()
    uid = get_current_owner_user_pk()
    stmt = select(DocumentAnnotation).where(
        DocumentAnnotation.tenant_id == tid, DocumentAnnotation.pk == ann_id)
    if uid is not None:
        stmt = stmt.where(DocumentAnnotation.owner_user_id == uid)
    return db.scalar(stmt)


def update(db: Session, ann_id: int, *, note: str | None = None, color: str | None = None) -> dict | None:
    a = _get_owned(db, ann_id)
    if a is None:
        return None
    if note is not None:
        a.note = note
    if color is not None:
        a.color = color
    db.flush()
    return _to_dict(a)


def delete(db: Session, ann_id: int) -> bool:
    a = _get_owned(db, ann_id)
    if a is None:
        return False
    db.delete(a)
    db.flush()
    return True


def markdown_for_doc(db: Session, id_external: str) -> str | None:
    d = resolve_doc(db, id_external)
    if d is None:
        return None
    rows = list_for_doc(db, id_external) or []
    out = [f"# Highlights — {d.name}", ""]
    if not rows:
        out.append("_No highlights yet._")
        return "\n".join(out)
    cur_page = None
    for r in rows:
        if r["page"] != cur_page:
            cur_page = r["page"]
            out.append(f"## Page {cur_page}")
            out.append("")
        quote = " ".join((r["text"] or "").split())
        out.append(f"> {quote}" if quote else "> _(no text captured — image region)_")
        if r["note"]:
            out.append(">")
            out.append(f"> **Note:** {r['note']}")
        out.append("")
    return "\n".join(out)
