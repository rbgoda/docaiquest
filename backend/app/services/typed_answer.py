"""#4 · Typed answer contract (Kezhan Shi, "Stop returning text from RAG").

Instead of free text, the cross-doc answer LLM call is asked for a VALIDATED typed
object — {answer, answer_found, format, caveats} — via the gateway's structured mode.
Typing the output makes the model commit to an explicit "did I find it?" boolean and
surfaces uncertainty as caveats rather than burying a guess in prose. We then render
back to the same text the rest of the pipeline (guardrail, citations, persistence)
already expects, so nothing downstream — or the frontend — has to change.

Flag-gated: settings.typed_answer_enabled (default off). `generate()` is safe — on any
structured-output failure it falls back to a plain text call, so it can never be worse
than the free-text path.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass

log = logging.getLogger("docaiq.typed_answer")

_SCHEMA_HINT = (
    "\n\nReturn ONLY a JSON object with this exact shape (no prose outside it):\n"
    '{"answer": "<your answer, in the format the rules ask for>", '
    '"answer_found": <true|false — false when the evidence does NOT contain it>, '
    '"format": "<single|list|table|none>", '
    '"caveats": ["<short note only if the evidence is partial/ambiguous>", "..."]}\n'
    "When answer_found is false, put a one-line 'not in the retrieved evidence' explanation "
    "in answer and leave caveats empty. Never invent values to fill the object."
)


@dataclass
class TypedAnswer:
    answer: str
    answer_found: bool
    format: str
    caveats: list[str]

    def rendered(self) -> str:
        """Flatten back to the text the pipeline expects (answer + any caveats)."""
        txt = (self.answer or "").strip()
        cav = [c.strip() for c in (self.caveats or []) if c and c.strip()]
        if cav:
            txt += "\n\n_Note: " + "; ".join(cav) + "_"
        return txt.strip()


def _coerce(obj: dict) -> TypedAnswer | None:
    if not isinstance(obj, dict) or "answer" not in obj:
        return None
    fmt = str(obj.get("format") or "none").lower()
    if fmt not in ("single", "list", "table", "none"):
        fmt = "none"
    cav = obj.get("caveats") or []
    if not isinstance(cav, list):
        cav = [str(cav)]
    return TypedAnswer(
        answer=str(obj.get("answer") or ""),
        answer_found=bool(obj.get("answer_found", True)),
        format=fmt,
        caveats=[str(c) for c in cav][:4],
    )


def _parse_json(text: str) -> dict | None:
    txt = (text or "").strip()
    if not txt:
        return None
    if txt.startswith("```"):                       # tolerate ```json fences
        txt = txt.strip("`")
        txt = txt[txt.find("{"):txt.rfind("}") + 1]
    try:
        return json.loads(txt)
    except Exception:  # noqa: BLE001
        return None


def generate(db, system: str, user_block: str, *, max_tokens: int = 700,
             extra_terms=None, cache_system: bool = True) -> TypedAnswer | None:
    """Ask for a typed answer object. Routes through `doc_chat.llm_one_shot` so it
    inherits the SAME cost guard, tenant tier-1 model, PII redaction + audit ledger
    as the free-text path (the only variable is the output contract). Returns None
    on any failure — the caller falls back to the free-text answer."""
    from app.services import doc_chat as _dc
    try:
        text = _dc.llm_one_shot(
            db, system + _SCHEMA_HINT, user_block,
            max_tokens=max_tokens, extra_terms=extra_terms,
            structured=True, cache_system=cache_system,
        )
    except Exception as e:  # noqa: BLE001 — includes the 429 cost-guard; caller falls back
        log.warning("typed_answer: llm_one_shot failed: %s", e)
        return None
    obj = _parse_json(text)
    return _coerce(obj) if obj is not None else None
