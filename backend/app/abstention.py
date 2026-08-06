"""R1 · Calibrated abstention for document chat.

Rather than answer from thin/irrelevant evidence (and risk a confident
hallucination — unacceptable for an audit/compliance product), the chat returns
a safe `INSUFFICIENT_EVIDENCE` refusal. Decision signals, cheapest first:

  * too few retrieved passages            (`min_hits`)
  * top passage relevance below a floor   (`min_top_score` — OFF until R4 calibrates it)
  * the grounding guardrail STILL flags the answer after a regenerate (opt-in strict mode)

Pure-stdlib + offline-testable; thresholds live in settings so they're tunable.
Conservative by default — it standardizes the existing zero-evidence refusal and
adds opt-in strictness; it does NOT start refusing answers that previously worked.
"""
from __future__ import annotations

INSUFFICIENT = "INSUFFICIENT_EVIDENCE"


def refusal_message(*, n_docs: int | None = None) -> str:
    """User-facing safe refusal. Begins with the `INSUFFICIENT` sentinel so the
    API/UI can reliably detect an abstention (vs a normal answer)."""
    scope = f" across the {n_docs} document(s) in scope" if n_docs else ""
    return (
        f"{INSUFFICIENT} — I couldn't find enough in your documents{scope} to answer that "
        "confidently. Try rephrasing, narrowing to a specific document, or confirm the "
        "relevant file is uploaded and finished processing."
    )


def is_abstention(text: str | None) -> bool:
    """True if a chat answer is an abstention refusal."""
    return bool(text) and text.lstrip().startswith(INSUFFICIENT)


def assess_evidence(scores, *, min_hits: int = 1, min_top_score: float | None = None):
    """Decide whether retrieved evidence is too weak to answer.

    `scores` = per-passage relevance scores (or a list whose length is the hit
    count, with None where a numeric score isn't available). Returns
    (abstain: bool, reason: str). `min_top_score=None` disables the score floor.
    """
    scores = list(scores or [])
    n = len(scores)
    if n < max(0, min_hits):
        return True, f"only {n} relevant passage(s) (need >= {min_hits})"
    if min_top_score is not None:
        numeric = [s for s in scores if isinstance(s, (int, float))]
        if numeric and max(numeric) < min_top_score:
            return True, f"top relevance {max(numeric):.3f} < {min_top_score}"
    return False, ""


def abstain_after_guardrail(grounded: bool, *, hard: bool) -> bool:
    """Strict mode: refuse instead of answering when the grounding guardrail
    still flags the answer after a regenerate. Default (hard=False) keeps the
    softer 'verify against the source' caveat."""
    return bool(hard) and not grounded
