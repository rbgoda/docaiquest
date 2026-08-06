"""Tier router — confidence-based cascade.

For each task:
1. **Apply rules.** `force_tier_3` / `lock_tier_1` / `min_tier_2` / `use_cached`
   from `routing_config.rules` short-circuit the cascade.
2. **Run Tier 1.** Pick a model from the tier's weighted pool, call it.
   Parse structured output → extract `confidence`.
3. **Escalate** to Tier 2 if `confidence < thresholds.escalateT2`.
4. **Escalate** to Tier 3 if `confidence < thresholds.escalateT3`.
5. **Flag for human review** if final `confidence < thresholds.humanReview`.

Every call writes a `LLMCall` row keyed to the requirement + chat message
(if any). The router returns the *winning* response plus a trace of every
tier it visited.

Cost calculation uses the per-model `cost` from `routing_config.tiers[*].models`
which is $/1M tokens — matches the data model M2 already shipped.
"""

from __future__ import annotations

import logging
import random
import re
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.db import get_current_tenant
from app.llm import gateway, ledger
from app.repositories import routing_configs as rc_repo

log = logging.getLogger("docaiq.llm.router")


@dataclass
class RoutingDecision:
    text: str
    structured: dict | None
    confidence: float | None
    final_tier: str
    final_model: str
    needs_human: bool
    calls: list[dict] = field(default_factory=list)  # per-tier trace


# ---- Entry point ----------------------------------------------------------
def route(
    db: Session,
    *,
    task: str,
    messages: list[gateway.Message],
    requirement_id_external: str | None = None,
    chat_message_pk: int | None = None,
    max_tokens: int = 512,
) -> RoutingDecision:
    """Run the cascade for `task` ('validate' | 'classify' | 'report').
    Returns the winning response + a trace of every tier visited."""
    tenant_id = get_current_tenant()
    # M36 · free-tier hourly rate cap on LLM calls. Raises 429 when over.
    # No-op for paid tenants. Committed at end of route().
    from app.plan_limits import check_and_record_llm_call
    check_and_record_llm_call(db)
    cfg = rc_repo.get(db) or {}
    tiers_by_id = {t["id"]: t for t in cfg.get("tiers", [])}
    thresholds = cfg.get("thresholds", {})
    rules = cfg.get("rules", [])

    # Build the cascade plan after rule evaluation.
    plan = _plan_cascade(task, messages, rules, tiers_by_id)

    # M37 · free tenants are locked to tier 1 regardless of their routing
    # config. This insulates the shared free container from any paid models
    # mixed into t2/t3 (e.g. Sonnet on T3 for hard cases) and caps per-call
    # cost on the shared key. No-op for paid tenants (own container, own keys).
    from app.plan_limits import is_free_tenant
    if is_free_tenant(db) and "t1" in tiers_by_id:
        plan = [tiers_by_id["t1"]]

    log.info("Routing task=%s plan=%s", task, [t["id"] for t in plan])

    calls: list[dict] = []
    last_decision: RoutingDecision | None = None

    for tier in plan:
        model_id, model_cfg = _pick_model(tier)
        if model_id is None:
            log.warning("Tier %s has no available models; skipping", tier["id"])
            continue
        try:
            # M44.P11 · pass tenant + task so gateway.call applies PII
            # redaction (when enabled) and writes the audit row. Without
            # this the whole cascade (matcher/validator, agent reasoning,
            # report gen, llm_one_shot fallback) bypasses both.
            result = gateway.call(
                model_id, messages, structured=True, max_tokens=max_tokens,
                tenant_id=tenant_id, task_kind=task,
            )
        except Exception as e:  # network / 5xx / parsing — log and try next tier
            log.warning("Provider call failed (%s): %s", model_id, e)
            _persist_call(db, tenant_id, task, tier["id"], None, model_id, None, None,
                          status="failed", error=str(e)[:500],
                          requirement_id_external=requirement_id_external,
                          chat_message_pk=chat_message_pk)
            calls.append({"tier": tier["id"], "model": model_id, "status": "failed", "error": str(e)[:200]})
            continue

        confidence = _extract_confidence(result)
        cost = _calc_cost(model_cfg, result.input_tokens, result.output_tokens)

        # Empty-content responses (reasoning-only models, hit max_tokens
        # mid-thought) count as escalate-worthy — there's no answer to show.
        empty_content = not (result.text or "").strip()
        if empty_content:
            log.info("Tier %s returned empty content; escalating if possible.", tier["id"])

        # We mark this row "escalated" if there's still a higher tier to try
        # AND (a) confidence didn't clear the gate, or (b) content was empty.
        will_escalate = empty_content or _should_escalate(tier["id"], confidence, thresholds, plan)
        # If we'd escalate but there's no next tier, accept what we have.
        if will_escalate and _next_tier(tier["id"], plan) is None:
            will_escalate = False
        status = "escalated" if will_escalate else "ok"

        _persist_call(
            db, tenant_id, task, tier["id"], result, model_id, cost, confidence,
            status=status,
            requirement_id_external=requirement_id_external,
            chat_message_pk=chat_message_pk,
        )
        calls.append({
            "tier": tier["id"], "model": model_id, "provider": result.provider,
            "tokens_in": result.input_tokens, "tokens_out": result.output_tokens,
            "latency_ms": result.latency_ms, "cost_usd": cost,
            "confidence": confidence, "status": status,
        })

        last_decision = RoutingDecision(
            text=result.text,
            structured=result.structured,
            confidence=confidence,
            final_tier=tier["id"],
            final_model=model_id,
            needs_human=False,
            calls=calls,
        )
        if not will_escalate:
            break

    if last_decision is None:
        # All providers failed. Return an empty decision; caller decides what to do.
        return RoutingDecision(
            text="", structured=None, confidence=None,
            final_tier="none", final_model="none",
            needs_human=True, calls=calls,
        )

    # Final humanReview check on the *winning* tier's confidence.
    human_threshold = thresholds.get("humanReview", 0.50)
    last_decision.needs_human = (
        last_decision.confidence is not None
        and last_decision.confidence < human_threshold
    )
    return last_decision


# ---- Cascade planning -----------------------------------------------------
def _plan_cascade(
    task: str,
    messages: list[gateway.Message],
    rules: list[dict],
    tiers_by_id: dict[str, dict],
) -> list[dict]:
    """Apply force/lock rules to decide which tiers (and in what order) to try.
    Default plan is t1 → t2 → t3."""
    text_blob = " ".join(m.content for m in messages).lower()

    forced: str | None = None
    minimum: str | None = None
    locked: str | None = None
    for rule in sorted(rules, key=lambda r: r.get("priority", 99)):
        if not rule.get("active", True):
            continue
        action = rule.get("action", "")
        cond = rule.get("condition", "").lower()
        if not _rule_matches(action, cond, task, text_blob):
            continue
        if action == "force_tier_3":
            forced = "t3"
        elif action == "lock_tier_1":
            locked = "t1"
        elif action == "min_tier_2":
            minimum = "t2"
        # `use_cached` would short-circuit entirely; skipped at this scope —
        # cache lookup happens before route() in the caller.

    if forced and forced in tiers_by_id:
        return [tiers_by_id[forced]]
    if locked and locked in tiers_by_id:
        return [tiers_by_id[locked]]

    default = ["t1", "t2", "t3"]
    if minimum:
        # Skip everything below the floor.
        i = default.index(minimum)
        default = default[i:]
    return [tiers_by_id[t] for t in default if t in tiers_by_id]


def _rule_matches(action: str, cond: str, task: str, text_blob: str) -> bool:
    """Coarse rule-condition matcher. The seeded rules in routing_config use
    simple shapes (`task == 'classify'`, `doc.text matches /HIPAA.../`). We
    pattern-match those forms here rather than running a real expression
    evaluator — keeps the surface tiny while covering the rules we ship."""
    if "task ==" in cond:
        wanted = cond.split("task ==")[-1].strip().strip("'\"")
        return task == wanted
    if "matches /" in cond:
        # Extract the regex body between slashes.
        body = cond.split("/")[1] if cond.count("/") >= 2 else ""
        try:
            import re
            return bool(re.search(body, text_blob, flags=re.IGNORECASE))
        except re.error:
            return False
    # Vendor-scoped rules (`vendor.id == '...'`) — not applied in M9; would
    # need the requirement's vendor id threaded through here.
    return False


# ---- Model selection inside a tier ---------------------------------------
def _pick_model(tier: dict) -> tuple[str | None, dict | None]:
    """Weighted pick from a tier's `models`. Status=`fallback` only kicks in
    when no `active` model exists. Filters out models we can't actually call
    (i.e. provider keys missing → backend resolves to stub, which we allow
    only if no other model is configured)."""
    actives = [m for m in tier.get("models", []) if m.get("status") == "active"]
    pool = actives if actives else tier.get("models", [])
    if not pool:
        return None, None
    weights = [max(0, m.get("weight", 1)) for m in pool]
    if sum(weights) == 0:
        weights = [1] * len(pool)
    chosen = random.choices(pool, weights=weights, k=1)[0]
    return chosen["id"], chosen


def _should_escalate(
    tier_id: str, confidence: float | None, thresholds: dict, plan: list[dict]
) -> bool:
    if confidence is None:
        # No confidence reported (provider didn't return structured) → escalate
        # only if there's a next tier; otherwise accept what we have.
        return _next_tier(tier_id, plan) is not None
    next_tid = _next_tier(tier_id, plan)
    if next_tid is None:
        return False
    threshold_map = {
        "t1": thresholds.get("escalateT2", 0.75),
        "t2": thresholds.get("escalateT3", 0.60),
    }
    threshold = threshold_map.get(tier_id)
    return threshold is not None and confidence < threshold


def _next_tier(current: str, plan: list[dict]) -> str | None:
    ids = [t["id"] for t in plan]
    if current not in ids:
        return None
    i = ids.index(current)
    return ids[i + 1] if i + 1 < len(ids) else None


# ---- Confidence + cost helpers -------------------------------------------

# Prose-mode confidence: matches `Confidence: 0.XX` (or `confidence = 0.85`)
# Anchored to a line near the END of the reply so we don't pick up things
# like "I am 95% confident that..." earlier in the prose. The validator's
# system prompt explicitly tells the model to put this on its OWN final
# line, so a tail-anchored match is the right shape.
_CONFIDENCE_TAIL_RE = re.compile(
    r"confidence\s*[:=]\s*([01](?:\.\d+)?|0?\.\d+)\b",
    re.IGNORECASE,
)


def _extract_confidence(result: gateway.CompletionResult) -> float | None:
    """Extract the model's confidence (0.0–1.0) from a CompletionResult.

    Two paths in priority order:
      1. JSON-mode `structured.confidence` (paid providers — Anthropic /
         Google native, when the request used `structured=True`).
      2. Prose-mode `Confidence: 0.XX` tag on the last line (the validator
         agent's contract — works on every provider including OpenRouter
         free-tier models where JSON mode is unreliable).

    Returning None means "no confidence reported" and the router will
    escalate to the next tier. Before this fallback existed, every prose
    reply from OpenRouter returned None → every cascade tier escalated →
    silent 3× cost regression hiding behind the routing UI.
    """
    if result.structured:
        raw = result.structured.get("confidence")
        if raw is not None:
            try:
                return max(0.0, min(1.0, float(raw)))
            except (TypeError, ValueError):
                pass
    if result.text:
        # Take the LAST match — the validator's contract puts it at the
        # end; tolerating earlier mentions ("at 50% confidence the data
        # is correct, ... Confidence: 0.72") avoids false-positives on
        # the prose body.
        matches = list(_CONFIDENCE_TAIL_RE.finditer(result.text))
        if matches:
            try:
                return max(0.0, min(1.0, float(matches[-1].group(1))))
            except (TypeError, ValueError):
                pass
    return None


def _calc_cost(model_cfg: dict | None, input_tok: int, output_tok: int) -> float:
    """Compute USD cost for a single completion.

    Reads `costIn` / `costOut` from `routing_config.tiers[*].models` as
    `$/1M input tokens` / `$/1M output tokens` respectively. Falls back
    to the legacy single `cost` field (a blended rate) when split rates
    are absent so existing routing configs keep working.

    Real provider pricing has up to 5× spread between input and output
    rates (Claude Haiku: $1/M in, $5/M out). Output-heavy fact
    extraction was under-reporting by 2-3× under the blended rate.
    """
    if not model_cfg:
        return 0.0
    rate_in = model_cfg.get("costIn")
    rate_out = model_cfg.get("costOut")
    if rate_in is not None or rate_out is not None:
        return (input_tok * float(rate_in or 0.0) + output_tok * float(rate_out or 0.0)) / 1_000_000
    rate = float(model_cfg.get("cost", 0))
    return (input_tok + output_tok) * rate / 1_000_000


def _persist_call(
    db: Session,
    tenant_id: str,
    task: str,
    tier: str,
    result: gateway.CompletionResult | None,
    model: str,
    cost: float | None,
    confidence: float | None,
    *,
    status: str,
    error: str | None = None,
    requirement_id_external: str | None,
    chat_message_pk: int | None,
) -> None:
    """Cascade-tier persistence. Delegates to `ledger.record_call` so the
    LLMCall lands in its own transaction — the customer's card was
    charged even when the surrounding request rolls back."""
    ledger.record_call(
        db,
        task=task,
        tier=tier,
        provider=result.provider if result else "n/a",
        model=model,
        input_tokens=result.input_tokens if result else 0,
        output_tokens=result.output_tokens if result else 0,
        cost_usd=cost,
        latency_ms=result.latency_ms if result else 0,
        confidence=confidence,
        status=status,
        error=error,
        requirement_id_external=requirement_id_external,
        chat_message_pk=chat_message_pk,
        tenant_id=tenant_id,
    )
