"""Tool · final_answer · terminator.

The agent loop ends when the LLM emits this tool. Args are the user-visible
answer text and a list of citation chunk_pks (or [E#] markers borrowed from
search_chunks observations).

The agent loop itself handles persistence + critic. This tool just shapes
the terminator payload so the loop knows we're done.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

NAME = "final_answer"
DESCRIPTION = (
    "TERMINATOR. Emit this when you have the answer. Args: "
    "text (the answer for the reviewer) and citations (list of chunk_pk ints "
    "you actually used)."
)
PARAMS_SCHEMA = {
    "type": "object",
    "properties": {
        "text": {"type": "string", "description": "The final answer text."},
        "citations": {
            "type": "array",
            "items": {"type": "integer"},
            "description": "List of chunk_pk integers cited.",
        },
    },
    "required": ["text"],
}


def call(*, db: Session, tenant_id: str, doc_id: str, text: str = "", citations: list | None = None, **_: object) -> dict:
    return {
        "is_terminator": True,
        "text": str(text or ""),
        "citations": [int(c) for c in (citations or []) if isinstance(c, (int, str)) and str(c).isdigit()],
    }
