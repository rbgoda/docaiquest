"""M46 · Documents chat guardrail — checks user chat INPUT and the model OUTPUT.

* `guard_input(text)`  — deterministic (zero-LLM) screen for prompt-injection /
  jailbreak / "reveal your system prompt" attempts. Returns a safe refusal
  string when the question is an attack, else None.
* `critique(db, question, evidence, answer)` — one cheap LLM pass that checks the
  answer is FULLY grounded in the evidence and on-topic (no invented numbers, no
  wrong-type documents — e.g. an insurance cert listed as a national ID).
  Returns (grounded: bool, issue: str). The caller regenerates once on a flag,
  then appends a "verify against the source" caveat if it still doesn't ground.
"""
from __future__ import annotations

import re

from sqlalchemy.orm import Session

_INJECTION_RX = re.compile(
    r"(ignore (all |the |your |any )?(previous|prior|above|earlier) (instruction|prompt|rule)"
    r"|disregard (the|your|all|any) (instruction|prompt|rule)"
    r"|you are now\b|act as (?:a |an )?(?:dan|jailbreak)"
    r"|reveal (your |the )?(system )?(prompt|instructions)"
    r"|what (is|are) your (system )?(prompt|instructions)"
    r"|print (your|the) (system )?(prompt|instructions)"
    r"|do anything now|developer mode|jailbreak)",
    re.I,
)

_REFUSAL = (
    "I can only help with questions about your documents — I can't change my "
    "instructions or reveal system prompts. Ask me about a document's content "
    "and I'll answer from your files."
)


def guard_input(text: str) -> str | None:
    """Return a refusal string if the question is a prompt-injection / jailbreak
    attempt; else None. Deterministic, zero LLM."""
    if text and _INJECTION_RX.search(text):
        return _REFUSAL
    return None


def critique(db: Session, question: str, evidence: str, answer: str,
             extra_terms: "list[tuple[str, str]] | None" = None) -> tuple[bool, str]:
    """One cheap LLM pass: is `answer` fully supported by `evidence` and on-topic
    for `question`? Returns (grounded, issue). Fail-OPEN — if the critique call
    itself errors, we treat the answer as grounded (never block on guard failure).

    `extra_terms` must match what the answer-generation call used, so PII
    tokenization is identical across evidence + answer — otherwise the same value
    redacts to different tokens here and the reviewer false-flags a real match."""
    from app.services import doc_chat as svc
    from app.llm.prompts import get_prompt
    sys_prompt = get_prompt("chat_guard_output")
    user = (
        f"QUESTION:\n{question}\n\nEVIDENCE:\n{evidence[:6000]}\n\nANSWER:\n{answer[:3000]}"
    )
    try:
        verdict = (svc.llm_one_shot(db, sys_prompt, user, max_tokens=120, extra_terms=extra_terms) or "").strip()
    except Exception:  # noqa: BLE001 — never block the answer on a guard failure
        return True, ""
    if verdict.upper().startswith("FLAG"):
        issue = verdict.split(":", 1)[-1].strip() if ":" in verdict else "answer not fully grounded"
        return False, issue[:200]
    return True, ""
