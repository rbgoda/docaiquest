"""M44.P12 · Overall-documents chat — cross-document Q&A endpoints.

Powers the "Ask across all documents" panel in the Documents tab. Scope is a
vendor's document set (the docs shown in that tab); pass `vendor_pk` to scope,
omit it for the tenant-wide thread.

Endpoints (admin/reviewer):
  GET  /api/workspace-chat?vendor_pk=<pk>   → thread + in-scope doc count
  POST /api/workspace-chat/messages         → new question → AI reply w/ citations
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.security import CurrentUser, require_role
from app.services import workspace_chat as svc

log = logging.getLogger(__name__)
router = APIRouter()


# ── Pydantic shapes ──────────────────────────────────────────────────────
class WorkspaceCitation(BaseModel):
    # chunkPk/page are optional: RAG citations carry them (chunk-level), but agent + type-listing
    # citations are DOCUMENT-level (docId/docName/field) with no chunk — they'd 500 the response if
    # these stayed required.
    chunkPk: int | None = None
    page: int | None = None
    docId: str | None = None
    docName: str | None = None
    field: str | None = None
    bbox: dict | None = None
    quote: str | None = None
    # R3 · the answer sentence this source backs + its support score (optional).
    sentence: str | None = None
    support: float | None = None
    supported: bool | None = None


class WorkspaceStep(BaseModel):
    i: int | None = None
    tool: str
    status: str | None = None      # ok | error | confirm
    ms: int | None = None
    summary: str | None = None


class WorkspaceArtifact(BaseModel):
    type: str                      # csv | xlsx
    filename: str | None = None
    content: str | None = None
    encoding: str | None = None    # None = text; "base64" = binary
    mime: str | None = None


class WorkspaceMessage(BaseModel):
    id: int
    role: str
    text: str
    citations: list[WorkspaceCitation] = []
    confidence: float | None = None
    createdAt: str | None = None
    meta: str | None = None
    # M51 · agentic chat · step trace + downloadable artifacts (CSV).
    trace: list[WorkspaceStep] = []
    artifacts: list[WorkspaceArtifact] = []


class WorkspaceThread(BaseModel):
    workspaceKey: str
    docCount: int
    messages: list[WorkspaceMessage] = []


class PostMessagePayload(BaseModel):
    text: str
    vendorPk: int | None = None
    # Subset mode · restrict retrieval to these document id_externals.
    # None/empty = the full vendor workspace.
    docIds: list[str] | None = None
    # Which saved conversation this message belongs to (None = the base thread).
    conv: str | None = None


class WorkspaceThreadSummary(BaseModel):
    conv: str | None = None
    title: str
    count: int = 0
    updatedAt: str | None = None


# ── Endpoints ────────────────────────────────────────────────────────────
@router.get("/workspace-chat", response_model=WorkspaceThread)
def get_workspace_chat(
    vendor_pk: int | None = Query(default=None),
    conv: str | None = Query(default=None),
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_role("admin", "reviewer")),
) -> dict:
    return svc.get_thread(db, user.org_id, vendor_pk, conv_id=conv)


@router.get("/workspace-chat/threads", response_model=list[WorkspaceThreadSummary])
def list_workspace_threads(
    vendor_pk: int | None = Query(default=None),
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_role("admin", "reviewer")),
) -> list[dict]:
    """This user's saved cross-document conversations (base thread + every `:c:<id>`
    conversation), newest first — powers the chat history picker."""
    return svc.list_threads(db, user.org_id, vendor_pk)


@router.delete("/workspace-chat")
def reset_workspace_chat(
    vendor_pk: int | None = Query(default=None),
    conv: str | None = Query(default=None),
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_role("admin", "reviewer")),
) -> dict:
    """Delete ONE of the signed-in user's cross-document conversations (the one named
    by `conv`, or the base thread). Owner-scoped — only ever wipes this user's own
    conversation, never their documents."""
    removed = svc.clear_thread(db, user.org_id, vendor_pk, conv_id=conv)
    return {"cleared": removed}


@router.post("/workspace-chat/messages", response_model=WorkspaceMessage)
def post_workspace_message(
    payload: PostMessagePayload,
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_role("admin", "reviewer")),
) -> dict:
    text = (payload.text or "").strip()
    if not text:
        from fastapi import HTTPException
        raise HTTPException(status_code=422, detail="Empty message")
    # Plan enforcement (LLM enabled + monthly AI cap) — every chat surface.
    from app.documents_scope import get_current_owner_user_pk
    from app.services import subscriptions as subs
    _uid = get_current_owner_user_pk()
    if _uid is not None:
        subs.enforce_chat(db, tenant_id=user.org_id, owner_user_id=_uid)
    return svc.post_message(db, user.org_id, payload.vendorPk, text, doc_ids=payload.docIds,
                            conv_id=payload.conv)
