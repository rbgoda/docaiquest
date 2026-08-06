"""Validator agent.

Given a user question + a requirement (optional) → builds a prompt with
retrieved evidence chunks → asks the router → returns a structured response.

Prompt strategy: natural-language answer with a final `Confidence: 0.XX`
line we regex-extract. JSON mode proved too brittle on free-tier models —
gpt-oss / gemma / qwen-free either ignore `response_format` or emit
malformed JSON. Plain text with a single tagged confidence line works on
every model we've tested, paid or free.

This is the agent driving chat in Review.jsx. It deliberately stays thin
(no loops, no tool use). Multi-step agentic reasoning (Reporter, Comparison)
lands later once observability is in place to debug it.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.llm import gateway
from app.llm.prompts import get_prompt
from app.llm.router import RoutingDecision, route
from app.retrieval import Hit, retrieve

log = logging.getLogger("docaiq.agents.validator")

_SYSTEM_PROMPT = """\
You are DocAIQ's Validator — a compliance audit assistant.

Your job: answer the user's question about a specific compliance
requirement using ONLY the evidence excerpts provided. Never invent
claims. If the evidence is insufficient, say so plainly.

CRITICAL · Document scoping. The user is asking about ONE requirement,
which is tied to AT MOST ONE attached document. When the user mentions
a specific document type (Aadhaar, passport, utility bill, etc.) in
their question, only cite excerpts FROM THAT document type. NEVER
summarize values from multiple documents in one answer ("the Aadhaar
says X but the passport says Y" — this confuses the auditor and
hallucinates cross-doc joins the user didn't ask for). If the excerpts
include unrelated documents, ignore them and answer from the relevant
one only.

Write 2–4 sentences in editorial prose. Cite specific evidence ids
(like `chunk-12`) in-line when you reference them.

Then on its OWN LINE at the very end of your reply, put a single
confidence score in this exact form:

Confidence: 0.XX

Confidence rubric:
  ≥ 0.85 — evidence directly answers the question
  0.60 – 0.84 — evidence supports the answer with minor caveats
  0.40 – 0.59 — partial evidence; some inference required
  < 0.40 — evidence is missing, contradictory, or off-topic
"""

# Matches confidence in TWO forms:
#   1. Natural language: "Confidence: 0.85"
#   2. JSON-mode:        "\"confidence\": 0.85"   (Gemini / Anthropic structured)
# The optional ['"\\] before/after handles quoted JSON keys + values.
_CONFIDENCE_RE = re.compile(
    r"confidence['\"]?[\s:,]*['\"]?([0-9]*\.?[0-9]+)", re.IGNORECASE,
)


@dataclass
class ValidatorResponse:
    answer: str
    confidence: float | None
    bullets: list[dict]
    citations: list[str]
    reasoning: list[str]
    hits: list[Hit]
    decision: RoutingDecision


def validate(
    db: Session,
    *,
    user_message: str,
    requirement_id_external: str | None,
    requirement_title: str | None = None,
    chat_message_pk: int | None = None,
    top_k: int = 6,
    doc_id_external: str | None = None,
    system_prompt: str | None = None,
    max_tokens: int = 1500,
) -> ValidatorResponse:
    hits = retrieve(db, user_message, top_k=top_k, doc_id_external=doc_id_external)

    evidence_block = _format_evidence(hits)
    req_block = (
        f"Requirement under review: {requirement_id_external or 'n/a'} — "
        f"{requirement_title or '(no title supplied)'}\n\n"
    )
    user_block = f"User question: {user_message}\n\n{evidence_block}"
    messages = [
        gateway.Message(role="system", content=system_prompt or get_prompt("validator")),
        gateway.Message(role="user", content=req_block + user_block),
    ]

    decision = route(
        db,
        task="validate",
        messages=messages,
        requirement_id_external=requirement_id_external,
        chat_message_pk=chat_message_pk,
        max_tokens=max_tokens,
    )

    answer, confidence = _parse_natural_reply(decision.text)
    if confidence is None:
        confidence = decision.confidence  # JSON-mode happy path (paid models)
    citations = _extract_citation_ids(answer)

    return ValidatorResponse(
        answer=answer or "(model returned no text — retry, or try a more specific question)",
        confidence=confidence,
        bullets=_synthesize_bullets(answer, citations),
        citations=citations,
        reasoning=[],
        hits=hits,
        decision=decision,
    )


# ---- Helpers ---------------------------------------------------------------
def _format_evidence(hits: list[Hit]) -> str:
    if not hits:
        return (
            "No matching evidence in this tenant's corpus. "
            "If the answer depends on documents, upload them first; "
            "otherwise answer from general compliance knowledge but lower confidence accordingly."
        )
    lines = ["Evidence excerpts (cite by id when used):"]
    for h in hits:
        ev_id = f"chunk-{h.chunk_pk}"
        lines.append(f"[{ev_id}] (doc: {h.document_name}, page {h.page})\n{h.text.strip()}\n")
    return "\n".join(lines)


def _parse_natural_reply(text: str) -> tuple[str, float | None]:
    """Strip the trailing 'Confidence: 0.XX' line and return (answer, conf)."""
    if not text:
        return "", None
    cleaned = re.sub(r"^\s*(answer|response)[\s:]+", "", text, flags=re.IGNORECASE)
    matches = list(_CONFIDENCE_RE.finditer(cleaned))
    if not matches:
        return cleaned.strip(), None
    m = matches[-1]
    try:
        c = float(m.group(1))
        c = max(0.0, min(1.0, c))
    except (TypeError, ValueError):
        c = None
    answer = (cleaned[: m.start()] + cleaned[m.end():]).strip()
    answer = re.sub(r"\n\s*confidence[^\n]*$", "", answer, flags=re.IGNORECASE).strip()
    return answer, c


def _extract_citation_ids(text: str) -> list[str]:
    if not text:
        return []
    return list(dict.fromkeys(re.findall(r"chunk-\d+", text)))


def _synthesize_bullets(answer: str, citations: list[str]) -> list[dict]:
    """Cite-rows for the existing UI. Free-tier models won't reliably
    structure bullets so we fabricate one per cited chunk."""
    if not citations:
        return []
    return [
        {"label": str(i + 1), "text": f"Source: {c}", "cite": c}
        for i, c in enumerate(citations[:5])
    ]
