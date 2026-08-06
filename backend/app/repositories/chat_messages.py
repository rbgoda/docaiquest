from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_current_tenant
from app.orm import ChatMessage


def _to_dict(row: ChatMessage) -> dict:
    # `citations` carries [{chunk_pk, page, bbox, quote}] for AI replies that
    # cited specific evidence chunks. M40 · full-bbox rollout lets the Review
    # screen draw a numbered yellow box on the right-panel document for each
    # citation — same overlay machinery as the highlights table. NULL on user
    # messages and on AI replies that didn't cite anything specific.
    return {
        "pk": row.pk,
        "role": row.role,
        "text": row.text,
        "bullets": row.bullets,
        "confidence": row.confidence,
        "trace": row.trace,
        "tools": row.tools,
        "citations": row.citations,
        "meta": row.meta,
    }


def list_all_grouped(db: Session) -> dict[str, list[dict]]:
    """Group requirement-scoped chat messages by their requirement id.

    Doc-scoped chat messages (from the Documents tab's chat panel) have
    requirement_id_external=NULL — they're keyed by doc_id_external
    instead and live in their own thread fetched per-doc. We filter them
    out here so the Review screen's `conversations[req_id]` dict only
    carries requirement-scoped messages and so the response shape
    matches `dict[str, list[Message]]` (None keys would fail Pydantic).
    """
    tid = get_current_tenant()
    rows = db.scalars(
        select(ChatMessage)
        .where(
            ChatMessage.tenant_id == tid,
            ChatMessage.requirement_id_external.isnot(None),
        )
        .order_by(ChatMessage.pk)
    ).all()
    out: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        out[r.requirement_id_external].append(_to_dict(r))
    return dict(out)


def list_for_requirement(db: Session, requirement_id_external: str) -> list[dict]:
    tid = get_current_tenant()
    rows = db.scalars(
        select(ChatMessage)
        .where(
            ChatMessage.tenant_id == tid,
            ChatMessage.requirement_id_external == requirement_id_external,
        )
        .order_by(ChatMessage.pk)
    ).all()
    return [_to_dict(r) for r in rows]


def append(db: Session, requirement_id_external: str, messages: list[dict]) -> list[dict]:
    """Insert one or more messages in order. Returns them with the same shape
    they came in with (the DB doesn't add anything the caller needs)."""
    tid = get_current_tenant()
    rows = [
        ChatMessage(
            tenant_id=tid,
            requirement_id_external=requirement_id_external,
            role=m["role"],
            text=m["text"],
            bullets=m.get("bullets"),
            confidence=m.get("confidence"),
            trace=m.get("trace"),
            tools=m.get("tools"),
            # M40 · persist citations so the doc-viewer overlay reads them
            # back on Review-screen reload (not only on the immediate send
            # roundtrip). Callers that don't pass citations get NULL.
            citations=m.get("citations"),
            meta=m.get("meta"),
        )
        for m in messages
    ]
    db.add_all(rows)
    db.flush()
    return [_to_dict(r) for r in rows]
