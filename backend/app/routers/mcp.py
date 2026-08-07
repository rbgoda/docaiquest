"""DocAIQuest MCP server — Model Context Protocol over Streamable HTTP.

Lets a user query THEIR OWN documents from any MCP client (Claude, ChatGPT, Cursor, an agent, or the
customer's own chatbot). Auth is an owner-scoped API key (created in the user's account → API keys),
sent as `Authorization: Bearer dq_live_…` or `X-API-Key`. Every tool reuses the exact product brain —
the same RAG + deterministic handlers the app uses — so answers are grounded in the caller's documents
and nothing else. Hand-rolled JSON-RPC 2.0 (no extra dependency); implements initialize / tools/list /
tools/call / ping.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from app.api_clients import Caller, require_client
from app.db import get_session

log = logging.getLogger("docaiq.mcp")
router = APIRouter()

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "docaiq", "version": "1.0.2", "title": "DocAIQuest — your documents"}

TOOLS = [
    {"name": "ask_documents",
     "description": "Ask a natural-language question about the user's own documents (invoices, "
                    "statements, IDs, contracts, etc.). Returns a grounded answer with source citations; "
                    "says it's not found rather than guessing.",
     "inputSchema": {"type": "object",
                     "properties": {"question": {"type": "string", "description": "The question to answer."}},
                     "required": ["question"]}},
    {"name": "list_documents",
     "description": "List the user's documents with id, name, type and date.",
     "inputSchema": {"type": "object",
                     "properties": {"limit": {"type": "integer", "description": "Max documents (default 50)."}}}},
    {"name": "get_watchlist",
     "description": "Upcoming deadlines, renewals and expiries derived from the user's documents "
                    "(e.g. passport expiry, contract end, payment due).",
     "inputSchema": {"type": "object", "properties": {}}},
]


def _ask(db: Session, owner_pk: int, args: dict) -> str:
    from app.routers.api_v1 import _rag_answer_for_owner
    r = _rag_answer_for_owner(db, owner_pk, (args or {}).get("question", ""), int((args or {}).get("topK", 8)))
    txt = (r.get("answer") or "").strip() or "I couldn't find that in your documents."
    cites = r.get("citations") or []
    if cites:
        names = list(dict.fromkeys(c.get("name") for c in cites if c.get("name")))
        if names:
            txt += "\n\nSources: " + ", ".join(names)[:400]
    return txt


def _list_documents(db: Session, owner_pk: int, args: dict) -> str:
    from app.orm import Document
    limit = max(1, min(int((args or {}).get("limit", 50)), 200))
    rows = db.query(Document).filter(
        Document.owner_user_id == owner_pk, Document.ingestion_status == "ready").order_by(
        Document.created_at.desc()).limit(limit).all()
    if not rows:
        return "You have no processed documents yet."
    lines = [f"You have {len(rows)} document(s):"]
    lines += [f"- {d.name} ({(d.doc_type or 'unclassified').replace('_', ' ')})" for d in rows]
    return "\n".join(lines)


def _watchlist(db: Session, owner_pk: int, args: dict) -> str:
    try:
        from app.documents_scope import set_current_owner_user_pk
        set_current_owner_user_pk(owner_pk)
        from app.routers.assistant import _derive_items
        items = _derive_items(db)
    except Exception as e:  # noqa: BLE001
        log.warning("mcp watchlist failed: %s", e)
        items = []
    if not items:
        return "Nothing on your watchlist — no upcoming dates were detected in your documents."
    out = ["Upcoming items from your documents:"]
    for it in items[:25]:
        d = it if isinstance(it, dict) else getattr(it, "__dict__", {})
        title = d.get("title") or d.get("label") or "Item"
        date = d.get("date") or ""
        out.append(f"- {title}: {date}".rstrip())
    return "\n".join(out)


_DISPATCH = {"ask_documents": _ask, "list_documents": _list_documents, "get_watchlist": _watchlist}
# Per-tool scope so granular keys can't bypass their grant once scope granularity exists.
_TOOL_SCOPES = {"ask_documents": "ask", "list_documents": "documents:read", "get_watchlist": "documents:read"}
_MAX_Q = 4000  # cap tool input length (DoS / cost guard)


def _err(rid, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}


@router.get("")
def mcp_get() -> Response:
    # We don't support server→client SSE streaming; clients fall back to POST-only, which is fine.
    return Response(status_code=405, headers={"Allow": "POST"})


@router.post("")
async def mcp_endpoint(request: Request,
                       caller: Caller = Depends(require_client()),
                       db: Session = Depends(get_session)):
    """Single Streamable-HTTP JSON-RPC endpoint. Requires an owner-scoped key."""
    if caller.owner_user_id is None:
        raise HTTPException(status_code=403,
                            detail="MCP needs an owner-scoped key (create one in your account → API keys)")
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return _err(None, -32700, "parse error")
    method = body.get("method")
    rid = body.get("id")
    params = body.get("params") or {}

    if method == "initialize":
        return {"jsonrpc": "2.0", "id": rid, "result": {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": SERVER_INFO}}
    if method in ("notifications/initialized", "initialized"):
        return Response(status_code=202)
    if method == "ping":
        return {"jsonrpc": "2.0", "id": rid, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}}
    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        fn = _DISPATCH.get(name)
        if fn is None:
            return _err(rid, -32602, f"unknown tool: {name}")
        need = _TOOL_SCOPES.get(name)
        granted = set(caller.scopes or [])
        if need and "*" not in granted and need not in granted:
            return _err(rid, -32604, f"key is missing the '{need}' scope for this tool")
        if isinstance(args.get("question"), str):
            args["question"] = args["question"][:_MAX_Q]
        try:
            text = fn(db, caller.owner_user_id, args)
        except Exception as e:  # noqa: BLE001 — never echo internals to the caller
            log.warning("mcp tool %s failed: %s", name, e)
            return {"jsonrpc": "2.0", "id": rid, "result": {
                "content": [{"type": "text", "text": "The tool hit an internal error — please try again."}],
                "isError": True}}
        return {"jsonrpc": "2.0", "id": rid, "result": {"content": [{"type": "text", "text": text}]}}
    return _err(rid, -32601, f"method not found: {method}")
