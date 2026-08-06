"""M44.P11 · LLM call audit ledger.

Records one row per `gateway.call()` invocation with HASHES of the
prompt + response (not the content) so we have a tamper-evident log
without becoming a PII custodian ourselves.

Why hashes instead of content:
  · Storing prompts/responses just moves the data-residency problem
    from the LLM provider to our DB. Hashes prove the call happened
    without re-imposing PII handling on us.
  · If we ever need to forensically reproduce a call (e.g., to verify
    a bug report), the original chunks are still in document_chunks
    and the routing config tells us which model. We can replay.

The recorder is best-effort: a DB failure NEVER raises into the caller.
Better to lose one audit row than to break the user-facing chat call.

Tenant scoping is by `tenant_id` column (passed in, not from contextvar,
because gateway.call is called from many different code paths some of
which don't have a request context).

Provider residency lookup ships here so other modules don't have to
duplicate it.
"""
from __future__ import annotations

import logging
from typing import Final

log = logging.getLogger("docaiq.llm_audit")


# Where each backend physically processes traffic. Surfaced on every
# audit row so compliance can answer "where did this tenant's data
# go?" with a SQL filter.
PROVIDER_RESIDENCY: Final[dict[str, str]] = {
    "openrouter": "global",   # multi-region; provider-of-providers
    "anthropic":  "US",       # us-east primary
    "google":     "US",       # us-central primary
    "dashscope":  "CN/SG",    # intl endpoint is in SG; CN backend
    "stub":       "local",
}


def record_call(
    *,
    tenant_id: str,
    user_email: str | None,
    provider: str,
    model: str,
    task_kind: str | None,
    doc_id_external: str | None,
    prompt_sha256: str,
    response_sha256: str | None,
    input_tokens: int | None,
    output_tokens: int | None,
    pii_entities_redacted: int = 0,
    pii_kinds: dict[str, int] | None = None,
    latency_ms: int | None,
    http_status: int | None = None,
    failure_kind: str | None = None,
) -> None:
    """Insert one audit row. Best-effort · NEVER raises.

    Each parameter is required, but many are nullable in the schema · we
    accept None to make this safe to call from places that don't have
    the full set of context (e.g. the curator job).
    """
    try:
        from app.db import SessionLocal
        from app.orm import LLMCallAudit
        residency = PROVIDER_RESIDENCY.get(provider, "unknown")
        with SessionLocal() as db:
            db.add(LLMCallAudit(
                tenant_id=tenant_id,
                user_email=user_email,
                provider=provider,
                model=model[:128],
                task_kind=task_kind,
                doc_id_external=doc_id_external,
                prompt_sha256=prompt_sha256,
                response_sha256=response_sha256,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                pii_entities_redacted=pii_entities_redacted,
                pii_kinds=pii_kinds,
                data_residency=residency,
                latency_ms=latency_ms,
                http_status=http_status,
                failure_kind=failure_kind,
            ))
            db.commit()
    except Exception as e:  # noqa: BLE001 — never break the caller
        log.warning("llm_audit · record_call failed (non-fatal): %s", e)
