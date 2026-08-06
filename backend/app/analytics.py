"""Tenant-scoped customer-facing analytics.

This module powers what *customers* see — the Dashboard KPIs, the Routing
Admin estimate cards, the Settings → Audit log timeline. Strictly
tenant-filtered through `get_current_tenant()`.

NOT for cross-tenant or fleet-wide observability — that's the future
operator-facing milestone (Prometheus / Grafana / Sentry / OTel). The data
visible here belongs to the customer whose session is active.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_current_tenant
from app.orm import (
    AuditRun,
    AuditRunRequirement,
    ChatMessage,
    Document,
    LLMCall,
    Requirement,
    RequirementRFI,
    User,
    UserRole,
)

Window = Literal["24h", "7d", "30d", "all"]


def _window_start(window: Window) -> datetime | None:
    """Convert window enum → cutoff timestamp. `all` → no filter."""
    now = datetime.now(timezone.utc)
    return {
        "24h": now - timedelta(hours=24),
        "7d": now - timedelta(days=7),
        "30d": now - timedelta(days=30),
        "all": None,
    }[window]


# ---- LLM spend ------------------------------------------------------------
def llm_spend(db: Session, window: Window = "7d") -> dict:
    """Total spend + per-tier + per-provider breakdown. All in USD,
    tenant-scoped via the contextvar."""
    tid = get_current_tenant()
    start = _window_start(window)

    base = select(LLMCall).where(LLMCall.tenant_id == tid)
    if start is not None:
        base = base.where(LLMCall.created_at >= start)
    base_sub = base.subquery()

    totals = db.execute(
        select(
            func.coalesce(func.sum(base_sub.c.cost_usd), 0.0),
            func.coalesce(func.sum(base_sub.c.input_tokens + base_sub.c.output_tokens), 0),
            func.count(),
        )
    ).one()
    total_cost, total_tokens, total_calls = totals

    by_tier_rows = db.execute(
        select(
            base_sub.c.tier,
            func.sum(base_sub.c.cost_usd),
            func.sum(base_sub.c.input_tokens + base_sub.c.output_tokens),
            func.count(),
        ).group_by(base_sub.c.tier)
    ).all()
    by_tier = [
        {"tier": t, "costUsd": float(c or 0), "tokens": int(tok or 0), "calls": int(n or 0)}
        for t, c, tok, n in by_tier_rows
    ]

    by_provider_rows = db.execute(
        select(
            base_sub.c.provider,
            func.sum(base_sub.c.cost_usd),
            func.count(),
        ).group_by(base_sub.c.provider)
    ).all()
    by_provider = [
        {"provider": p, "costUsd": float(c or 0), "calls": int(n or 0)}
        for p, c, n in by_provider_rows
    ]

    return {
        "window": window,
        "totalCostUsd": float(total_cost or 0),
        "totalTokens": int(total_tokens or 0),
        "totalCalls": int(total_calls or 0),
        "byTier": by_tier,
        "byProvider": by_provider,
    }


# ---- Routing performance --------------------------------------------------
def routing_stats(db: Session, window: Window = "7d") -> dict:
    """Tier mix %, average cost per audit (extrapolated from llm_calls),
    estimated savings vs all-Tier-3. Powers the four cards at the top of
    Routing Admin."""
    tid = get_current_tenant()
    start = _window_start(window)

    base = select(LLMCall).where(LLMCall.tenant_id == tid)
    if start is not None:
        base = base.where(LLMCall.created_at >= start)
    sub = base.subquery()

    rows = db.execute(
        select(
            sub.c.tier,
            func.count(),
            func.coalesce(func.sum(sub.c.cost_usd), 0.0),
            func.coalesce(func.avg(sub.c.latency_ms), 0),
            func.coalesce(func.avg(sub.c.confidence), 0.0),
        ).group_by(sub.c.tier)
    ).all()

    total_calls = sum(int(r[1] or 0) for r in rows) or 1
    total_cost = sum(float(r[2] or 0) for r in rows)
    weighted_acc = sum(float(r[1] or 0) * float(r[4] or 0) for r in rows) / total_calls
    # Crude p50 latency proxy — avg of avg over tiers. Real p50 is a M-later concern.
    avg_latency = sum(int(r[3] or 0) for r in rows) / max(1, len(rows))

    tier_mix = [
        {
            "tier": tier,
            "share": int(n or 0) / total_calls,
            "calls": int(n or 0),
            "costUsd": float(c or 0),
        }
        for tier, n, c, _, _ in rows
    ]

    # `est cost / audit` — we don't have an explicit "this call belongs to
    # audit X" link yet (requirement_id_external is the closest), so we just
    # compute average cost per requirement that's been touched in the window.
    requirements_touched = db.scalar(
        select(func.count(func.distinct(sub.c.requirement_id_external)))
    ) or 0
    est_cost_per_audit = (total_cost / requirements_touched) if requirements_touched else 0.0

    # Savings vs always-Tier-3: how much MORE we'd have paid if every call
    # had gone to T3. Reads the T3 rate from the routing config — best-effort;
    # if no T3 model in config, this returns 0.
    est_savings = _estimate_savings_vs_t3(db, sub)

    return {
        "window": window,
        "tierMix": tier_mix,
        "avgLatencyMs": int(avg_latency),
        "estAccuracy": float(weighted_acc),
        "estCostPerAuditUsd": float(est_cost_per_audit),
        "estDailySavingsUsd": float(est_savings),
    }


def _estimate_savings_vs_t3(db: Session, sub) -> float:
    """How much more we'd have spent if every call had used Tier 3's most-
    expensive model. Useful for the 'Daily savings vs all-Opus' card."""
    from app.repositories import routing_configs as rc_repo

    cfg = rc_repo.get(db) or {}
    t3 = next((t for t in cfg.get("tiers", []) if t["id"] == "t3"), None)
    if not t3 or not t3.get("models"):
        return 0.0
    # Pick the most expensive T3 model as the "what if we used premium" baseline.
    t3_rate = max(float(m.get("cost", 0)) for m in t3["models"])

    actual_cost, total_tokens = db.execute(
        select(
            func.coalesce(func.sum(sub.c.cost_usd), 0.0),
            func.coalesce(func.sum(sub.c.input_tokens + sub.c.output_tokens), 0),
        )
    ).one()
    hypothetical = float(total_tokens or 0) * t3_rate / 1_000_000
    return max(0.0, hypothetical - float(actual_cost or 0))


# ---- Activity feed --------------------------------------------------------
def activity_feed(db: Session, limit: int = 20) -> list[dict]:
    """Union of recent events across (verdicts, chat messages, uploads, LLM
    escalations) — newest first. Cheaper to build in Python than as a single
    monster SQL query for now; row counts are tiny."""
    tid = get_current_tenant()
    events: list[dict] = []

    # Verdict updates · per-(audit_run, requirement). The verdict columns
    # live on AuditRunRequirement, not Requirement — the requirement table
    # only carries the framework-level metadata (title, group, AI status).
    # `verdict_at` is a string today, so we order with nulls_last for safety.
    verdicts = db.execute(
        select(
            Requirement.id_external.label("req_id"),
            Requirement.title.label("title"),
            AuditRunRequirement.verdict,
            AuditRunRequirement.verdict_at,
            AuditRunRequirement.verdict_by,
        )
        .join(Requirement, Requirement.pk == AuditRunRequirement.requirement_pk)
        .where(
            AuditRunRequirement.tenant_id == tid,
            AuditRunRequirement.verdict.isnot(None),
        )
        .order_by(AuditRunRequirement.verdict_at.desc().nulls_last())
        .limit(limit)
    ).all()
    for r in verdicts:
        events.append({
            "type": "verdict",
            "at": r.verdict_at,
            "actor": r.verdict_by,
            "summary": f"{r.verdict} · {r.req_id}",
            "detail": r.title,
        })

    # Recent user-chat sends (skip ai responses — too noisy)
    chats = db.execute(
        select(ChatMessage.requirement_id_external, ChatMessage.text, ChatMessage.created_at)
        .where(ChatMessage.tenant_id == tid, ChatMessage.role == "user")
        .order_by(ChatMessage.created_at.desc())
        .limit(limit)
    ).all()
    for c in chats:
        events.append({
            "type": "chat",
            "at": c.created_at.isoformat() if c.created_at else None,
            "actor": None,
            "summary": f"question on {c.requirement_id_external}",
            "detail": c.text[:120],
        })

    # Recent doc uploads (those with sha256 set, i.e. real uploads)
    uploads = db.execute(
        select(Document.id_external, Document.name, Document.uploaded_by,
               Document.ingestion_status)
        .where(Document.tenant_id == tid, Document.sha256.isnot(None))
        .order_by(Document.pk.desc())
        .limit(limit)
    ).all()
    for d in uploads:
        events.append({
            "type": "upload",
            "at": None,  # documents.created_at not stamped today; M11 cleanup
            "actor": d.uploaded_by,
            "summary": f"uploaded {d.name}",
            "detail": f"ingestion: {d.ingestion_status or 'pending'}",
        })

    # LLM escalations — interesting because they mean a Tier 1 model failed
    # the confidence gate and we paid for a higher tier.
    escalations = db.execute(
        select(LLMCall.tier, LLMCall.model, LLMCall.confidence,
               LLMCall.requirement_id_external, LLMCall.created_at)
        .where(LLMCall.tenant_id == tid, LLMCall.status == "escalated")
        .order_by(LLMCall.created_at.desc())
        .limit(limit)
    ).all()
    for c in escalations:
        events.append({
            "type": "escalation",
            "at": c.created_at.isoformat() if c.created_at else None,
            "actor": "AI",
            "summary": f"escalated past {c.tier.upper()}",
            "detail": f"{c.model} returned conf={c.confidence:.2f} → next tier"
                      if c.confidence is not None else f"{c.model} → next tier",
        })

    # Sort all-events by `at` desc; rows missing timestamps go last.
    events.sort(key=lambda e: (e.get("at") or ""), reverse=True)
    return events[:limit]


# ---- Audit log (admin Settings tab) --------------------------------------
def audit_log(db: Session, *, limit: int = 100, offset: int = 0, event_type: str | None = None) -> dict:
    """Same source data as activity feed but paginated + filterable. Two
    consumers but one materialized view — keeps the implementation honest."""
    all_events = activity_feed(db, limit=limit + offset + 50)
    if event_type:
        all_events = [e for e in all_events if e["type"] == event_type]
    page = all_events[offset : offset + limit]
    return {"events": page, "total": len(all_events), "offset": offset, "limit": limit}


# ---- Audit posture (Dashboard top KPIs) ----------------------------------
def audit_posture(db: Session) -> dict:
    """Dashboard-style aggregate. Currently computed in the frontend from
    /api/audit-runs; exposing here so the UI doesn't have to do the math."""
    from app.orm import AuditRun
    tid = get_current_tenant()
    rows = db.execute(
        select(
            func.coalesce(func.sum(AuditRun.total), 0),
            func.coalesce(func.sum(AuditRun.compliant), 0),
            func.coalesce(func.sum(AuditRun.review), 0),
            func.coalesce(func.sum(AuditRun.missing), 0),
            func.coalesce(func.sum(AuditRun.pending), 0),
            func.count(),
        ).where(AuditRun.tenant_id == tid)
    ).one()
    total, compliant, review, missing, pending, runs = rows
    return {
        "activeAudits": int(runs or 0),
        "total": int(total or 0),
        "compliant": int(compliant or 0),
        "needsReview": int(review or 0),
        "missing": int(missing or 0),
        "pending": int(pending or 0),
    }


# ---- Reviewer workload (admin monitoring) -----------------------------------
def reviewer_stats(db: Session) -> list[dict]:
    """One row per reviewer in this tenant. Aggregates:
      • audit_runs they're the lead on
      • verdicts they've set across all audit_run_requirements
      • RFIs they've raised / responded to / resolved
      • pending requirements across their audits

    Admin-monitoring view; tenant-scoped (NEVER cross-tenant). Returns even
    reviewers with zero activity so the table doesn't silently hide a new hire
    who hasn't been assigned anything yet.
    """
    tid = get_current_tenant()

    # All users in this tenant who hold the `reviewer` role (or owner / admin
    # — they also do review work). Distinct set; we'll show empty-zero rows
    # for anyone who hasn't done anything yet.
    reviewer_rows = db.execute(
        select(User.pk, User.email, User.name, func.array_agg(UserRole.role).label("roles"))
        .join(UserRole, UserRole.user_pk == User.pk)
        .where(User.tenant_id == tid)
        .where(UserRole.role.in_(("owner", "admin", "reviewer")))
        .group_by(User.pk, User.email, User.name)
    ).all()

    # Audit runs grouped by lead_reviewer email
    audit_rows = db.execute(
        select(AuditRun).where(AuditRun.tenant_id == tid)
    ).scalars().all()
    audits_by_lead: dict[str, list[AuditRun]] = {}
    for ar in audit_rows:
        audits_by_lead.setdefault(ar.lead_reviewer, []).append(ar)

    # Verdict counts per reviewer (verdict_by email is the reviewer who set it)
    verdict_counts = dict(db.execute(
        select(AuditRunRequirement.verdict_by, func.count())
        .where(AuditRunRequirement.tenant_id == tid)
        .where(AuditRunRequirement.verdict_by.is_not(None))
        .group_by(AuditRunRequirement.verdict_by)
    ).all())

    # RFI raised + resolved counts per reviewer
    rfi_raised = dict(db.execute(
        select(RequirementRFI.raised_by, func.count())
        .where(RequirementRFI.tenant_id == tid)
        .group_by(RequirementRFI.raised_by)
    ).all())
    rfi_resolved = dict(db.execute(
        select(RequirementRFI.resolved_by, func.count())
        .where(RequirementRFI.tenant_id == tid)
        .where(RequirementRFI.resolved_by.is_not(None))
        .group_by(RequirementRFI.resolved_by)
    ).all())

    # Pending requirements per reviewer = unverdicted across their audits.
    # Map audit_run_pk → lead_reviewer for a join-free aggregation.
    audit_pk_to_lead = {ar.pk: ar.lead_reviewer for ar in audit_rows}
    pending_by_lead: dict[str, int] = {}
    for arr_pk, arr_verdict, ar_pk in db.execute(
        select(AuditRunRequirement.pk, AuditRunRequirement.verdict, AuditRunRequirement.audit_run_pk)
        .where(AuditRunRequirement.tenant_id == tid)
    ).all():
        if arr_verdict is None:
            lead = audit_pk_to_lead.get(ar_pk)
            if lead:
                pending_by_lead[lead] = pending_by_lead.get(lead, 0) + 1

    out: list[dict] = []
    for r in reviewer_rows:
        my_audits = audits_by_lead.get(r.email, [])
        out.append({
            "email": r.email,
            "name": r.name,
            "roles": sorted(set(r.roles)),
            "auditsAssigned": [
                {
                    "id": a.id_external,
                    "vendor": a.vendor,
                    "framework": a.framework,
                    "due": a.due,
                    "progress": a.progress,
                    "risk": a.risk,
                }
                for a in my_audits
            ],
            "auditCount": len(my_audits),
            "verdictsSet": int(verdict_counts.get(r.email, 0) or 0),
            "rfisRaised": int(rfi_raised.get(r.email, 0) or 0),
            "rfisResolved": int(rfi_resolved.get(r.email, 0) or 0),
            "pendingVerdicts": int(pending_by_lead.get(r.email, 0) or 0),
        })

    # Audits whose lead_reviewer doesn't match any seeded reviewer (typo, deleted
    # user) — surface them as "unassigned" so admin can re-assign.
    unassigned_audits = []
    known_emails = {r.email for r in reviewer_rows}
    for ar in audit_rows:
        if ar.lead_reviewer not in known_emails:
            unassigned_audits.append({
                "id": ar.id_external,
                "vendor": ar.vendor,
                "framework": ar.framework,
                "leadReviewer": ar.lead_reviewer,
                "due": ar.due,
            })

    # Sort: most assigned audits first, then by pending workload, then alpha.
    out.sort(key=lambda r: (-r["auditCount"], -r["pendingVerdicts"], r["email"]))
    return {"reviewers": out, "unassigned": unassigned_audits}
