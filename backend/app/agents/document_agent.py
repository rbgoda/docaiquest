"""M44.P2 · Document Agent · ReAct loop with NATIVE tool-use API.

The agent receives a question scoped to a single document and decides
which tool(s) to call to answer it. Uses the gateway's native tool_use
support (Anthropic/OpenAI function-calling) — the LLM receives JSON Schema
tool definitions and returns structured tool_calls that are ALWAYS valid JSON.

Persistence
-----------
* One AgentTrace row per step (thought + action + observation).
* One ChatMessage row per agent run (final answer).
* After terminator: optional Critic pass on the final answer, ReflexionPair
  row for learning. (Same flow the legacy single-shot uses, just over the
  agent's final answer instead of a single LLM draft.)

Fail-open
---------
If anything in the loop fails (LLM error, tool error, no tool_call in
response) the agent records the failure and forces a final_answer with
the best-effort text it has. The caller (doc_chat.post_message) wraps this
in its own try/except and falls back to the legacy single-shot path.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.agents import tools as tool_registry
from app.config import get_settings
from app.llm import gateway
from app.llm.prompts import get_prompt
from app.orm import AgentTrace, ChatMessage, Document

log = logging.getLogger("docaiq.document_agent")

MAX_STEPS = 8


@dataclass
class AgentStep:
    step_index: int
    thought: str | None = None
    action_name: str | None = None
    action_args: dict | None = None
    observation: str | None = None
    observation_meta: dict | None = None
    error: str | None = None
    latency_ms: int | None = None


@dataclass
class AgentResult:
    chat_message: ChatMessage
    steps: list[AgentStep] = field(default_factory=list)
    final_text: str = ""
    citations: list[int] = field(default_factory=list)


_SYSTEM_PROMPT = """\
You are DocAIQ Document Agent — a tool-using research agent for an audit \
compliance platform. You answer reviewer questions about a SINGLE uploaded \
document by calling tools step-by-step. Use the provided tools to look up \
information. Call ONE tool per turn. When you have enough information to \
answer the question, call final_answer immediately.

CRITICAL RULES — read carefully
  · **As soon as the answer is visible in any observation, call final_answer \
on the very next turn.** Do not keep searching.
  · **get_extracted_field paths are FLAT in most cases**, not dotted. The \
extractor stores fields under `fields.<name>` already. Do NOT invent nested \
paths like "invoice.invoice_number". When an observation includes \
`available_keys`, try one of those keys on the next turn.

GUIDELINES
  · Prefer get_extracted_field for typed values (IDs, dates, amounts) — \

faster + more reliable than search_chunks.
  · When returning an ID number, ALWAYS call validate_id_format on it before \
final_answer. If it returns a mismatch_hint, search for the correct value \
instead of returning the wrong one.
  · Cite specific chunk_pk values from search_chunks observations.
  · **Don't deflect prematurely.** A value the user asks for may be present even when the \
document is a DIFFERENT type than the question assumes — e.g. a passport number printed on a \
travel-authorization/ESTA, or a revenue figure inside a résumé. SEARCH for the value \
(schema_record + search_chunks) before answering "not applicable" or only correcting the \
document type. Only say a value is absent AFTER you have actually looked for it.
  · When a question names a field, try schema_record first — it lists every field (incl. ones \
derived from the envelope) so you can read the value even if get_extracted_field's exact key misses.
  · Maximum {max_steps} steps — be efficient.
  · When in doubt, call get_doc_summary first for orientation.

EXAMPLES OF FIELD PATHS
  · "fields.invoice_number"   ✓ correct (top-level field)
  · "invoice_number"          ✓ correct (the "fields." prefix is added automatically)
  · "invoice.invoice_number"  ✗ WRONG · there is no `invoice` parent object
  · "fields.dob"              ✓ correct
  · "fields.line_items"       ✓ correct (array of line items)
"""


def _resolve_agent_model(db: Session) -> str:
    """Pick the model for agent tool-use. Uses tenant routing config if available,
    falling back to the intelligence model or a default."""
    s = get_settings()
    # Use intelligence model (Dashscope Qwen-Max by default) — supports native tool-use
    model = getattr(s, "intelligence_model", None) or getattr(s, "documents_agent_model", None)
    if model and "/" not in model:
        model = f"dashscope/{model}"
    if model:
        return model
    # Fallback: resolve from tenant routing (same logic as llm_one_shot)
    try:
        from app.routing_config import repo as rc_repo
        cfg = rc_repo.get(db) or {}
        tiers = cfg.get("tiers") or []
        t1 = next((t for t in tiers if t.get("id") == "t1"), tiers[0] if tiers else None)
        if t1:
            for m in t1.get("models") or []:
                if m and m.get("status", "active") == "active" and m.get("id"):
                    return m["id"]
    except Exception:
        pass
    return "dashscope/qwen-max"


def _build_gateway_tools() -> list[dict]:
    """Build OpenAI/Anthropic-format tool definitions from the tool registry."""
    tools = []
    for t in tool_registry.ALL_TOOLS:
        tools.append({
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["params_schema"],
            },
        })
    return tools


def run(
    db: Session,
    doc: Document,
    question: str,
    *,
    tenant_id: str,
    max_steps: int = MAX_STEPS,
) -> AgentResult:
    """Execute the ReAct loop using NATIVE tool-use (no manual JSON parsing).
    Persists the final ChatMessage + AgentTrace rows + ReflexionPair."""
    model = _resolve_agent_model(db)
    tools = _build_gateway_tools()

    system = get_prompt("document_agent", max_steps=str(max_steps)) + (
        f"\n\nDOCUMENT: doc_id={doc.id_external}, type={doc.doc_type or 'unknown'}, "
        f"name={doc.name}. You have {max_steps} steps max."
    )

    # M44.P2 · feed prior reviewer-curated critiques into the agent's context
    reflexion_preamble = _safe_reflexion_few_shot(db, question)

    messages: list[gateway.Message] = []
    if reflexion_preamble:
        messages.append(gateway.Message(role="system", content=system + "\n\n" + reflexion_preamble))
    else:
        messages.append(gateway.Message(role="system", content=system))
    messages.append(gateway.Message(role="user", content=f"QUESTION: {question}"))

    steps: list[AgentStep] = []
    final_text = ""
    final_citations: list[int] = []
    forced_terminate = False

    for step_idx in range(max_steps):
        step = AgentStep(step_index=step_idx)
        t0 = time.perf_counter()

        try:
            result = gateway.call(
                model=model, messages=messages, temperature=0.2, max_tokens=400,
                tools=tools, tool_choice="auto",
            )
        except Exception as e:
            step.error = f"llm call failed: {e}"
            step.latency_ms = int((time.perf_counter() - t0) * 1000)
            steps.append(step)
            log.warning("agent step %d · llm failed: %s", step_idx, e)
            break

        # No tool_call in response → model answered in text (rare with tool_choice=auto)
        if not result.tool_calls:
            step.observation = result.text[:500]
            step.latency_ms = int((time.perf_counter() - t0) * 1000)
            steps.append(step)
            # If the model gave a direct answer, use it
            if result.text and len(result.text.strip()) > 20:
                final_text = result.text[:2000]
                break
            # Otherwise force synthesis from prior observations
            final_text = _synthesize_fallback_answer(steps)
            forced_terminate = True
            break

        tc = result.tool_calls[0]
        tool_name = tc.get("function", {}).get("name", "") or tc.get("name", "")
        try:
            tool_args = json.loads(tc.get("function", {}).get("arguments", "{}"))
        except json.JSONDecodeError:
            tool_args = {}

        step.thought = f"→ {tool_name}"
        step.action_name = tool_name
        step.action_args = tool_args

        # Terminator?
        if tool_name == "final_answer":
            obs = tool_registry.dispatch(
                "final_answer", db=db, tenant_id=tenant_id,
                doc_id=doc.id_external, args=tool_args,
            )
            final_text = str(obs.get("text") or "")
            final_citations = [int(c) for c in (obs.get("citations") or [])]
            step.observation = "(terminator)"
            step.observation_meta = {"text_len": len(final_text), "citations": final_citations}
            step.latency_ms = int((time.perf_counter() - t0) * 1000)
            steps.append(step)
            break

        # Regular tool dispatch
        try:
            obs = tool_registry.dispatch(
                tool_name, db=db, tenant_id=tenant_id,
                doc_id=doc.id_external, args=tool_args,
            )
            step.observation_meta = obs
            step.observation = _render_observation(obs)
        except KeyError:
            step.error = f"unknown tool: {step.action_name}"
            step.observation = (
                f"ERROR: tool '{step.action_name}' is not registered. "
                f"Available: {[t.name for t in tool_registry.all_tools()]}"
            )
        except Exception as e:  # noqa: BLE001
            step.error = f"tool error: {e}"
            step.observation = f"ERROR while running {step.action_name}: {e}"
            log.exception("agent step %d · tool %s failed", step_idx, step.action_name)

        step.latency_ms = int((time.perf_counter() - t0) * 1000)
        steps.append(step)

        # Append step to LLM-visible messages — assistant tool_call + tool result
        messages.append(gateway.Message(
            role="assistant",
            content=result.text or "",
            tool_calls=result.tool_calls,
        ))
        # Tool result must match the tool_call_id from the assistant's tool_calls
        tc_id = (result.tool_calls or [{}])[0].get("id", "") if result.tool_calls else ""
        messages.append(gateway.Message(
            role="tool",
            content=f"Tool result: {(step.observation or '')[:1500]}",
            tool_call_id=tc_id,
        ))

    if not final_text:
        # Loop hit MAX_STEPS without final_answer. Synthesize one from history.
        forced_terminate = True
        final_text = _synthesize_fallback_answer(steps)

    # Persist the agent's ChatMessage row
    msg = ChatMessage(
        tenant_id=tenant_id,
        requirement_id_external=None,
        doc_id_external=doc.id_external,
        role="ai",
        text=final_text,
        confidence=None,
        citations=_build_citation_objects(db, doc, final_citations),
        meta="agent" + (" · forced_terminate" if forced_terminate else ""),
    )
    db.add(msg)
    db.flush()

    # Persist trace rows
    for s in steps:
        db.add(AgentTrace(
            tenant_id=tenant_id,
            chat_message_pk=msg.pk,
            step_index=s.step_index,
            thought=s.thought,
            action_name=s.action_name,
            action_args=s.action_args,
            observation=s.observation,
            observation_meta=_safe_jsonb(s.observation_meta),
            error=s.error,
            latency_ms=s.latency_ms,
        ))
    db.flush()

    # M44.P2 · Persist a ReflexionPair so the 👍/👎 vote endpoint can find
    # this answer (matches on doc_id_external + final_answer) and so future
    # similar questions can inherit lessons via cosine retrieval. The
    # agent's "critique" is a compact summary of the tools it used + any
    # errors, since the agent doesn't run an external critic on its own
    # final answer (Phase 2.5 will add one).
    _persist_reflexion_for_agent(
        db,
        tenant_id=tenant_id,
        question=question,
        steps=steps,
        final_text=final_text,
        doc_id_external=doc.id_external,
        forced_terminate=forced_terminate,
    )

    return AgentResult(
        chat_message=msg,
        steps=steps,
        final_text=final_text,
        citations=final_citations,
    )


def _safe_reflexion_few_shot(db: Session, question: str) -> str:
    """Delegate to the service-layer reflexion few-shot retriever.
    Moved from routers/doc_chat.py → services/doc_chat.py to break
    a router→agent→router import cycle."""
    try:
        from app.services.doc_chat import build_reflexion_few_shot
        return build_reflexion_few_shot(db, question)
    except Exception as e:  # noqa: BLE001
        log.debug("agent reflexion few-shot failed (non-fatal): %s", e)
        return ""


def _persist_reflexion_for_agent(
    db: Session,
    *,
    tenant_id: str,
    question: str,
    steps: list[AgentStep],
    final_text: str,
    doc_id_external: str,
    forced_terminate: bool,
) -> None:
    """Insert a reflexion_pairs row capturing this agent run so reviewer
    feedback (👍/👎) has somewhere to land and the next similar question
    can benefit from this trace.

    Fail-open: errors are logged at WARNING; the run still succeeds."""
    try:
        from app.embeddings import embed as _embed_fn
        from app.orm import ReflexionPair

        # Compact "critique" summarising the trace — useful in the few-shot
        # preamble next time. Includes tool sequence + any errors.
        tool_seq = " → ".join(
            (s.action_name or "??") for s in steps if s.action_name
        ) or "(no tools called)"
        errors = [s.error for s in steps if s.error]
        critique_summary = f"agent · tools: {tool_seq}"
        if errors:
            critique_summary += f" · errors: {len(errors)} ({errors[0][:100]})"

        from app.documents_scope import get_current_owner_user_pk
        [q_vec] = _embed_fn([question])
        db.add(ReflexionPair(
            tenant_id=tenant_id,
            question=question,
            question_embed=q_vec,
            draft_answer=final_text,
            critique=critique_summary,
            final_answer=final_text,
            doc_id_external=doc_id_external,
            owner_user_id=get_current_owner_user_pk(),
            iterations=len(steps),
            # The "passed_on_first" semantic for the agent path:
            # true only when no errors occurred AND the loop terminated
            # via final_answer (not by hitting MAX_STEPS).
            passed_on_first=(not forced_terminate and not errors),
        ))
        db.flush()
    except Exception as e:  # noqa: BLE001
        log.warning("agent reflexion persist failed (non-fatal): %s", e)


# ---- Helpers --------------------------------------------------------------
# Deprecated: _parse_action is kept for workspace_agent compat. New code uses
# native tool-use via gateway.call(tools=...). Will be removed when workspace
# agent is also migrated.
def _parse_action(raw: str) -> dict | None:
    """[DEPRECATED] Tolerant JSON parser for manual tool-call responses."""
    s = (raw or "").strip()
    if s.startswith("```"):
        lines = s.split("\n")
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        s = "\n".join(lines).strip()
    a = s.find("{")
    b = s.rfind("}")
    if a < 0 or b < a:
        return None
    candidate = s[a:b + 1]
    try:
        obj = json.loads(candidate)
    except json.JSONDecodeError:
        try:
            import json_repair
            obj = json_repair.loads(candidate)
        except Exception:
            return None
    if not isinstance(obj, dict):
        return None
    if "tool" not in obj:
        return None
    return obj


def _render_observation(obs: dict) -> str:
    """Compact string view of a tool observation for the LLM history.
    Large list fields are truncated; everything else round-trips as JSON."""
    try:
        s = json.dumps(obs, default=str)
    except Exception:  # noqa: BLE001
        s = str(obs)
    return s[:1500]


def _safe_jsonb(obj: dict | None) -> dict | None:
    """Make sure the observation_meta is JSON-serialisable for JSONB."""
    if obj is None:
        return None
    try:
        json.dumps(obj, default=str)
        return obj
    except Exception:  # noqa: BLE001
        return {"_repr": str(obj)[:1500]}


def _synthesize_fallback_answer(steps: list[AgentStep]) -> str:
    """When the loop hits MAX_STEPS without final_answer, return a useful
    summary so the reviewer doesn't get an empty bubble."""
    snippets: list[str] = []
    for s in steps[-3:]:
        if s.observation:
            snippets.append(f"({s.action_name}) {s.observation[:300]}")
    if snippets:
        return (
            "The agent could not converge on a final answer within the step "
            "limit. Most recent findings:\n\n" + "\n\n".join(snippets)
        )
    return "The agent could not produce an answer for this question."


def _build_citation_objects(db: Session, doc: Document, chunk_pks: list[int]) -> list[dict]:
    """Convert a list of chunk_pk ints into the {chunk_pk, page, bbox} shape
    the frontend expects under ChatMessage.citations."""
    from sqlalchemy import select

    from app.orm import DocumentChunk

    if not chunk_pks:
        return []
    seen: set[int] = set()
    out: list[dict] = []
    for pk in chunk_pks:
        if pk in seen:
            continue
        seen.add(pk)
        ch = db.scalar(select(DocumentChunk).where(
            DocumentChunk.pk == pk,
            DocumentChunk.document_pk == doc.pk,
        ))
        if ch is None:
            continue
        out.append({
            # camelCase to match Pydantic Citation model · the frontend
            # also reads chunkPk. The Pydantic model accepts both via
            # AliasChoices for backward-compat with old DB rows.
            "chunkPk": int(ch.pk),
            "page": int(ch.page),
            "bbox": None,
            "quote": (ch.text or "")[:200],
        })
    return out
