"""LLM-call ledger writer — single place every paid call lands.

Why this module exists: the cascade `router.route()` records its calls
into the `llm_calls` table, but five agents (classifier, fact_extractor,
categorizer, kyc_extractor, ingestion_vision) historically called
OpenRouter directly and skipped the ledger entirely. That under-reported
tenant spend by ~80% — every upload runs 3-5 of those agents and zero
landed on the dashboard.

For now this is a thin write-through wrapper. When the gateway grows
multi-modal + JSON-schema + tool support and the agents migrate to
`gateway.call()`, this helper stays as the in-gateway hook — same
contract, same row shape, no migration needed at the call site.

Pricing note: OpenRouter prices input ≠ output (Claude Haiku: $1/M
input, $5/M output). The legacy `routing_config.tiers[*].models[].cost`
is a single $/1M rate that conflates the two. Callers that know the
real input/output rates can pass `cost_usd` directly; otherwise we
estimate from `cost_per_mtok` × (input + output) and accept the
under-report (matches today's behavior — see TODO #11 for the fix).
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.db import SessionLocal, get_current_tenant
from app.orm import LLMCall

log = logging.getLogger("docaiq.llm.ledger")


def record_call(
    db: Session,
    *,
    task: str,                                # "classify" | "extract" | "categorize" | "kyc" | "vision"
    tier: str,                                # "t1" | "t2" | "t3" — agents currently always "t2"
    provider: str,                            # "openrouter" | "anthropic" | "google" | "stub"
    model: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cost_usd: float | None = None,
    cost_per_mtok: float | None = None,         # legacy blended rate; prefer split rates below
    cost_per_input_mtok: float | None = None,   # $/1M input tokens (Anthropic Haiku: $1)
    cost_per_output_mtok: float | None = None,  # $/1M output tokens (Anthropic Haiku: $5 — 5×!)
    latency_ms: int = 0,
    confidence: float | None = None,
    status: str = "ok",                       # "ok" | "failed"
    error: str | None = None,
    requirement_id_external: str | None = None,
    chat_message_pk: int | None = None,
    document_pk: int | None = None,           # attribute this call's cost to a document
    tenant_id: str | None = None,             # override when no request context (worker startup)
) -> None:
    """Append one LLMCall row in its OWN transaction.

    The point of the ledger is "I want to know what was actually billed
    by the provider, even if the request that paid for it later failed."
    If we shared the caller's transaction, a 500 in the surrounding
    request handler would roll back the LLMCall along with the work —
    the customer's card was charged but our audit trail vanished.

    Implementation: every call opens a short-lived SessionLocal, writes
    one row, commits, closes. Adds one connection-pool checkout per
    LLM call but the queue depth is fine (LLM calls are already 100ms-60s
    of latency, so the connection overhead is in the noise).

    The `db` parameter is kept in the signature for backwards
    compatibility with #8 callers; it's intentionally unused — the
    ledger never touches the caller's session.

    Failures here are LOGGED, never raised — losing one ledger row must
    not bring down the agent's actual work. Aggregate spend stays
    representative even with occasional gaps.
    """
    _ = db  # intentionally unused — see docstring; ledger uses its own session
    try:
        tid = tenant_id if tenant_id is not None else get_current_tenant()
    except Exception as e:  # noqa: BLE001
        log.warning("ledger: no tenant context (%s) for task=%s model=%s", e, task, model)
        return
    if cost_usd is None:
        # Prefer the split (input × in-rate + output × out-rate). Most
        # provider pricing is 3-5× higher on output than input; the legacy
        # blended `cost_per_mtok` under-reports spend on extraction-heavy
        # tasks by 2-3×.
        if cost_per_input_mtok is not None or cost_per_output_mtok is not None:
            cin = float(cost_per_input_mtok or 0.0)
            cout = float(cost_per_output_mtok or 0.0)
            cost_usd = (input_tokens * cin + output_tokens * cout) / 1_000_000
        elif cost_per_mtok is not None:
            cost_usd = (input_tokens + output_tokens) * cost_per_mtok / 1_000_000
    try:
        with SessionLocal() as ledger_session:
            ledger_session.add(LLMCall(
                tenant_id=tid,
                requirement_id_external=requirement_id_external,
                document_pk=document_pk,
                chat_message_pk=chat_message_pk,
                task_type=task,
                tier=tier,
                provider=provider,
                model=model,
                input_tokens=int(input_tokens or 0),
                output_tokens=int(output_tokens or 0),
                cost_usd=float(cost_usd or 0.0),
                latency_ms=int(latency_ms or 0),
                confidence=confidence,
                status=status,
                error=(error or None) and str(error)[:500],
                trace=None,
            ))
            ledger_session.commit()
    except Exception as e:  # noqa: BLE001
        log.warning("ledger: record_call failed (%s) for task=%s model=%s", e, task, model)
