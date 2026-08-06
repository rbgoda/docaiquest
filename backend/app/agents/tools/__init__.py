"""M44.P2 · Tool registry for the Document Agent.

Each tool is a single module exporting:
  * NAME          — string used by the LLM to call the tool
  * DESCRIPTION   — shown to the LLM in the system prompt
  * PARAMS_SCHEMA — JSON-Schema describing args
  * call(db, *, tenant_id, doc_id, **args) -> dict observation

The registry collects all tools so the agent loop can:
  * render the tool catalog in the system prompt
  * dispatch a tool-name to its call function
  * include all tools in OpenAI-style tool definitions (future)

Tenant scoping is the caller's responsibility — the agent loop passes
tenant_id explicitly. Tools that hit the DB must filter by it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from sqlalchemy.orm import Session

from . import (
    cross_doc_search,
    final_answer,
    get_doc_summary,
    get_extracted_field,
    related_documents,
    schema_record,
    search_chunks,
    search_entities,
    validate_id_format,
)


@dataclass
class Tool:
    name: str
    description: str
    params_schema: dict
    call: Callable[..., dict]


_TOOL_MODULES = [
    search_chunks,
    get_extracted_field,
    schema_record,
    search_entities,
    related_documents,
    validate_id_format,
    get_doc_summary,
    cross_doc_search,
    final_answer,
]


def all_tools() -> list[Tool]:
    """Return the ordered list of registered tools."""
    return [
        Tool(
            name=m.NAME,
            description=m.DESCRIPTION,
            params_schema=m.PARAMS_SCHEMA,
            call=m.call,
        )
        for m in _TOOL_MODULES
    ]


# Pre-built list for native tool-use gateway calls (avoids re-building every agent run)
ALL_TOOLS: list[dict] = [
    {
        "name": m.NAME,
        "description": m.DESCRIPTION,
        "params_schema": m.PARAMS_SCHEMA,
    }
    for m in _TOOL_MODULES
]


def tool_by_name(name: str) -> Tool | None:
    for t in all_tools():
        if t.name == name:
            return t
    return None


def catalog_for_prompt() -> str:
    """Render a human-readable catalog the agent LLM can see. One line
    per tool with name, description, and arg names."""
    lines = []
    for t in all_tools():
        params = t.params_schema.get("properties") or {}
        arg_list = ", ".join(params.keys()) or "(none)"
        lines.append(f"  · {t.name}({arg_list}) — {t.description}")
    return "\n".join(lines)


def dispatch(
    name: str,
    *,
    db: Session,
    tenant_id: str,
    doc_id: str,
    args: dict[str, Any] | None = None,
) -> dict:
    """Resolve and invoke a tool by name. Returns the tool's observation
    dict. Raises KeyError if the tool isn't registered — callers should
    catch and turn that into an LLM-visible "unknown tool" observation."""
    tool = tool_by_name(name)
    if tool is None:
        raise KeyError(f"unknown tool: {name}")
    return tool.call(db=db, tenant_id=tenant_id, doc_id=doc_id, **(args or {}))
