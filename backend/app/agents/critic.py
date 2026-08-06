"""M43.P1.5 · Critic Agent · Hermes/Reflexion-style self-critique.

After the validator produces a draft answer, the critic reviews it
against the question + source chunks + doc metadata. If the answer is
wrong (wrong field returned, incomplete, contradicted by source), the
critic emits a structured CRITIQUE that drives a refinement pass.

Why this exists
---------------
User-reported failure mode: ask "what is the ID number?" on an Aadhaar
PDF, validator returns the Enrolment number ("2821/27042/00235")
because that's the first ID-like string in the chunks. Only when the
reviewer manually pushes back ("this is enrolment not aadhaar") does
the second pass return the actual Aadhaar number ("8737 4291 7380").

The critic catches that automatically by knowing common ID formats.
Not Aadhaar-specific — it knows every common government ID + business
identifier (passport / SSN / NRIC / PAN / GST / EIN / etc.) so the
same loop catches "passport vs visa number", "DUNS vs EIN", etc.

Critic always runs in 1-2 iterations max. If it can't get a PASS after
two refines, it returns the best draft with a "low confidence ·
critic flagged" badge so the reviewer knows.

Cost
----
One cheap LLM call per draft (Qwen-2.5-7B by default). Adds ~500ms to
chat latency. The 90% of answers that pass on first iteration short-
circuit the loop with no overhead beyond that single critic call.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass

from app.llm import gateway
from app.llm.prompts import get_prompt
from app.model_registry import REGISTRY as _AI_REGISTRY

log = logging.getLogger("docaiq.critic")

_DEFAULT_MODEL = os.environ.get(
    "DOCAIQ_CRITIC_MODEL",
    _AI_REGISTRY["chat_critic"].default_model,
)


@dataclass
class Critique:
    passes: bool
    reason: str
    suggestion: str
    corrected_hint: str | None = None


_CRITIC_SYSTEM = """\
You are a skeptical document-review critic for a compliance audit tool.
Given a user question, an AI's draft answer, source excerpts, and the
document's metadata, decide whether the draft is CORRECT and COMPLETE
for the question asked.

You are NOT generating the answer yourself. You are reviewing the draft.

CHALLENGE the draft against these common failure modes:

1. WRONG FIELD RETURNED
   The draft returns a field that LOOKS like the asked-for field but is
   actually a different one. Pay special attention to these ID formats:
     · Aadhaar (India)              · 12 digits, "NNNN NNNN NNNN"
     · Aadhaar Enrolment            · 14 digits, "NNNN/NNNNN/NNNNN"
     · PAN (India)                  · 10 chars, AAAAA9999A
     · NRIC (Singapore)             · S/T/F/G/M + 7 digits + letter
     · UEN (Singapore business)     · varies, often 9-10 alphanumeric
     · Passport                     · 6-9 alphanumeric, often starts letter
     · SSN (US)                     · NNN-NN-NNNN
     · EIN (US business)            · NN-NNNNNNN
     · DUNS                         · 9 digits
     · GSTIN (India)                · 15 chars, 2 digits + PAN + checksum
     · Driver licence               · varies by state/country
   If the draft returns one ID type when the question asked for a
   different one, FAIL with a corrected_hint pointing at the right one
   from the source excerpts.

2. INCOMPLETE
   Question asked for "all line items", "all parties", "all dates" etc.
   and the draft returned only some. Source excerpts contain more.

3. CONTRADICTED BY SOURCE
   Draft makes a claim the source excerpts directly contradict.

4. HALLUCINATION
   Draft asserts something that is not in any source excerpt and is not
   a reasonable inference. (Be lenient on summarisations / paraphrases.)

5. SCOPE MISMATCH
   Question asked about a specific party/date/entity, draft answered
   about a different one.

Respond with STRICT JSON ONLY · no preamble, no code fences:
{
  "passes": <true|false>,
  "reason": "<=120 char one-liner>",
  "suggestion": "<=200 char actionable hint for the next pass; empty if passes=true>",
  "corrected_hint": "<the actual correct answer from the source, or null>"
}

When in doubt about a borderline draft, PASS — the human reviewer will
catch what we miss. Only FAIL when you have specific evidence the
draft is wrong.
"""


def critique(
    *,
    question: str,
    draft: str,
    source_excerpts: list[str],
    doc_summary: str | None = None,
    doc_type: str | None = None,
    model: str | None = None,
) -> Critique:
    """Score the draft. Returns a Critique. Always returns SOMETHING — on
    LLM error we return PASS (fail-open) so the chat flow keeps working
    even when the critic is unavailable."""
    model_id = model or _DEFAULT_MODEL

    excerpts_joined = "\n\n---\n\n".join(
        (s or "")[:1200] for s in (source_excerpts or [])[:6]
    )
    meta_block = ""
    if doc_summary:
        meta_block += f"Document summary: {doc_summary}\n"
    if doc_type:
        meta_block += f"Document type (classifier): {doc_type}\n"

    user_msg = (
        f"{meta_block}\n"
        f"QUESTION FROM REVIEWER:\n{question}\n\n"
        f"AI'S DRAFT ANSWER:\n{draft}\n\n"
        f"SOURCE EXCERPTS FROM THE DOCUMENT (top retrieved chunks):\n"
        f"{excerpts_joined or '(none)'}\n\n"
        "Review the draft now. Strict JSON only."
    )

    # M44.P11 · tenant context → PII redaction + audit on the critic call.
    from app.db import get_current_tenant as _get_tid
    try:
        _critic_tid = _get_tid()
    except Exception:  # noqa: BLE001
        _critic_tid = None
    try:
        # M44.P3.B · _CRITIC_SYSTEM is ~1K-token ID-format table — stable
        # across every critic call. Flag for prompt caching (90% discount
        # on the cached prefix on Anthropic / Anthropic-via-OpenRouter).
        result = gateway.call(
            model_id,
            messages=[
                gateway.Message(role="system", content=get_prompt("critic")),
                gateway.Message(role="user", content=user_msg),
            ],
            temperature=0.0,
            max_tokens=400,
            structured=True,
            cache_system=True,
            tenant_id=_critic_tid,
            task_kind="critic",
        )
        raw = (result.text or "").strip()
    except Exception as e:  # noqa: BLE001
        log.warning("critic: LLM call failed: %s · fallback PASS", e)
        return Critique(passes=True, reason="critic unavailable · fail-open", suggestion="")

    parsed = _parse_critic_json(raw)
    if parsed is None:
        log.warning("critic: unparseable response · fallback PASS · raw=%r", raw[:200])
        return Critique(passes=True, reason="critic response unparseable · fail-open", suggestion="")

    return parsed


def _parse_critic_json(raw: str) -> Critique | None:
    """Tolerant parse · the LLM may wrap JSON in code fences or add
    preamble despite the instruction. Strip both before json.loads."""
    s = raw.strip()
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
        except Exception:  # noqa: BLE001
            return None
    if not isinstance(obj, dict):
        return None
    return Critique(
        passes=bool(obj.get("passes", True)),
        reason=str(obj.get("reason") or "")[:240],
        suggestion=str(obj.get("suggestion") or "")[:400],
        corrected_hint=(str(obj["corrected_hint"]) if obj.get("corrected_hint") else None),
    )
