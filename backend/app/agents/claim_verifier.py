"""R2 · Chain-of-Verification — per-claim faithfulness judge.

R3 (sentence_citations) attributes sentences to evidence by word overlap — cheap
and deterministic, but blind to paraphrase and entailment. R2 adds the *semantic*
check: decompose the answer into atomic claims (sentences) and have the LLM judge
each one strictly against the evidence, flagging (or dropping) the unsupported.

One LLM call judges ALL claims at once (per-claim verdicts), so cost stays ~1 call
per answer — the same budget as the existing whole-answer guardrail, but
claim-resolved. Fail-OPEN: any error treats claims as supported (never block an
answer on a verifier failure). The verdict PARSER is pure + offline-testable.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

from app.sentence_citations import split_sentences

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

_LINE = re.compile(r"^\s*\[?(\d+)\]?\s*[:.)-]\s*(.+)$")


def split_claims(answer: str) -> list[str]:
    """Atomic claims = sentences (reuses the R3 splitter). Drops a trailing
    soft-caveat line if present so we don't 'verify' our own disclaimer."""
    return [s for s in split_sentences(answer) if not s.lstrip("_").startswith("⚠")]


def parse_verdicts(text: str, n: int) -> list[tuple[bool, str]]:
    """Parse the LLM's per-claim verdict block into [(supported, reason), ...] of
    length `n`. Lines look like '1: SUPPORTED' or '3: UNSUPPORTED — reason'.
    Missing/garbled lines default to SUPPORTED (fail-open)."""
    verdicts: dict[int, tuple[bool, str]] = {}
    for line in (text or "").splitlines():
        m = _LINE.match(line)
        if not m:
            continue
        idx = int(m.group(1))
        body = m.group(2).strip()
        supported = "UNSUPPORTED" not in body.upper() and "NOT SUPPORTED" not in body.upper()
        reason = "" if supported else body.split("—", 1)[-1].strip(" —-:")[:160]
        verdicts[idx] = (supported, reason)
    return [verdicts.get(i + 1, (True, "")) for i in range(n)]


def verify(db: Session, claims: list[str], evidence: str,
           *, extra_terms: "list[tuple[str, str]] | None" = None) -> list[dict]:
    """Judge each claim against `evidence`. Returns [{claim, supported, reason}]."""
    if not claims:
        return []
    from app.services import doc_chat as svc
    numbered = "\n".join(f"{i + 1}. {c}" for i, c in enumerate(claims))
    sys_prompt = (
        "You verify each CLAIM strictly against the EVIDENCE for a document-Q&A "
        "assistant. A claim is SUPPORTED only if the evidence directly states or "
        "clearly entails it; otherwise UNSUPPORTED. Reply with EXACTLY one line per "
        "claim, in order: '<n>: SUPPORTED' or '<n>: UNSUPPORTED — <short reason>'. "
        "Tokens like [PERSON_1], [ACCOUNT_1] are redacted PII placeholders — a "
        "placeholder in a claim is supported when the same placeholder is in the "
        "evidence; never flag a matching placeholder."
    )
    user = f"EVIDENCE:\n{evidence[:6000]}\n\nCLAIMS:\n{numbered}"
    try:
        out = svc.llm_one_shot(db, sys_prompt, user, max_tokens=400, extra_terms=extra_terms) or ""
    except Exception:  # noqa: BLE001 — never block an answer on a verifier failure
        return [{"claim": c, "supported": True, "reason": ""} for c in claims]
    verdicts = parse_verdicts(out, len(claims))
    return [{"claim": claims[i], "supported": v[0], "reason": v[1]}
            for i, v in enumerate(verdicts)]


def summarize(verified: list[dict]) -> dict:
    """Roll up verify() output: {n, supported, unsupported, all_supported, flags:[reasons]}."""
    unsupported = [v for v in verified if not v["supported"]]
    return {
        "n": len(verified),
        "supported": len(verified) - len(unsupported),
        "unsupported": len(unsupported),
        "all_supported": not unsupported,
        "flags": [{"claim": v["claim"], "reason": v["reason"]} for v in unsupported],
    }


def drop_unsupported(verified: list[dict]) -> str:
    """Strict mode: rebuild the answer from only the supported claims."""
    return " ".join(v["claim"] for v in verified if v["supported"]).strip()
