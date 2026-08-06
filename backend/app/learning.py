"""Learning-loop analytics.

The matcher writes `requirement.confidence` when it scores a doc against
a requirement, and the reviewer writes `audit_run_requirements.verdict`
when they sign off. This module turns those two signals into the metrics
admins need to know whether the AI is calibrated, where it's wrong, and
whether the auto-approve threshold could be tightened or relaxed.

No actual model training happens here today — that's deferred until there
is enough verdict volume per tenant to justify the infra. The dashboard
gives admins real visibility into AI performance without overpromising.

Conventions:
  * `agreement` = AI's automatic decision matched the reviewer's verdict
      - AI attached a doc (`doc_id_external` set) AND reviewer approved
      - AI did not attach (or low-conf) AND reviewer rejected / needs-info
  * `disagreement` = AI was wrong in one direction
      - false_positive: AI auto-attached, reviewer rejected
      - false_negative: AI didn't attach, reviewer approved (manually
        attached evidence later)

All queries are tenant-scoped via `get_current_tenant()`.
"""
from __future__ import annotations


from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_current_tenant
from app.orm import AuditRun, AuditRunRequirement, Requirement


# ---- shared loader ---------------------------------------------------------

def _load_verdict_rows(db: Session) -> list[dict]:
    """One row per (audit_run, requirement) that has BOTH a reviewer verdict.
    Brings the matcher's signal alongside (doc_id_external, confidence) so
    each downstream function can compute against the same labelled sample
    set. Small enough (~hundreds to a few thousand rows per tenant) that
    pulling it whole and looping in Python is simpler than 5 separate SQLs."""
    tid = get_current_tenant()
    rows = db.execute(
        select(
            Requirement.id_external.label("req_id"),
            Requirement.title,
            Requirement.group,
            Requirement.confidence,
            Requirement.doc_id_external,
            AuditRunRequirement.verdict,
            AuditRunRequirement.verdict_at,
            AuditRunRequirement.verdict_by,
            AuditRunRequirement.verdict_reason,
            AuditRun.id_external.label("audit_id"),
            AuditRun.vendor,
            AuditRun.framework,
        )
        .join(AuditRunRequirement, AuditRunRequirement.requirement_pk == Requirement.pk)
        .join(AuditRun, AuditRun.pk == AuditRunRequirement.audit_run_pk)
        .where(
            Requirement.tenant_id == tid,
            AuditRunRequirement.verdict.is_not(None),
        )
    ).all()
    return [dict(r._mapping) for r in rows]


def _agreement(row: dict) -> bool:
    """AI's automatic decision matched the reviewer's verdict."""
    ai_attached = row["doc_id_external"] is not None
    approved = row["verdict"] == "approve"
    return (ai_attached and approved) or (not ai_attached and not approved)


def _framework_of(group: str | None) -> str:
    """Strip the section, keep the framework prefix.
    'SOC 2 · CC6 Logical Access' → 'SOC 2'. Unknown/empty → 'Other'."""
    if not group:
        return "Other"
    return (group.split(" · ", 1)[0] or "Other").strip() or "Other"


# ---- 1 · overall summary ---------------------------------------------------

def summary(db: Session) -> dict:
    """Top-of-dashboard KPIs. Returns counts + accuracy + a couple of
    derived counts the UI shows as supporting context (sample size matters
    when interpreting accuracy — 95% off 20 samples is noise)."""
    rows = _load_verdict_rows(db)
    total = len(rows)
    agree = sum(1 for r in rows if _agreement(r))
    disagree = total - agree
    fps = sum(1 for r in rows if r["doc_id_external"] is not None and r["verdict"] != "approve")
    fns = sum(1 for r in rows if r["doc_id_external"] is None and r["verdict"] == "approve")
    reviewers = {r["verdict_by"] for r in rows if r["verdict_by"]}
    return {
        "totalVerdicts": total,
        "agreement": agree,
        "disagreement": disagree,
        "accuracyPct": round((agree / total * 100), 1) if total else None,
        "falsePositives": fps,
        "falseNegatives": fns,
        "reviewerCount": len(reviewers),
    }


# ---- 2 · calibration (decile bins) -----------------------------------------

def calibration(db: Session) -> list[dict]:
    """Bin matcher confidence into deciles, compute approval rate per bin.

    Only includes rows where both a confidence score AND a reviewer
    verdict exist (the matcher hasn't necessarily scored every requirement
    a reviewer has touched, especially older seed data).

    A perfectly-calibrated matcher: bin N (confidence ~ N/10) → reviewer
    approves ~ N/10 of the time. The frontend overlays the ideal diagonal
    against the observed curve so miscalibration is visible at a glance."""
    rows = _load_verdict_rows(db)
    bins = [
        {
            "min": round(i / 10, 1),
            "max": round((i + 1) / 10, 1),
            "count": 0,
            "approve": 0,
        }
        for i in range(10)
    ]
    for r in rows:
        if r["confidence"] is None:
            continue
        c = float(r["confidence"])
        idx = 9 if c >= 1.0 else max(0, min(9, int(c * 10)))
        bins[idx]["count"] += 1
        if r["verdict"] == "approve":
            bins[idx]["approve"] += 1
    for b in bins:
        b["approvalRate"] = round(b["approve"] / b["count"], 3) if b["count"] else None
    return bins


# ---- 3 · per-framework accuracy --------------------------------------------

def per_framework(db: Session) -> list[dict]:
    """Same agreement metric, split by framework prefix of the requirement
    group. Surfaces frameworks where the AI underperforms so the admin can
    tighten the prompt or the auto-approve threshold for that framework."""
    rows = _load_verdict_rows(db)
    by_fw: dict[str, list[dict]] = {}
    for r in rows:
        by_fw.setdefault(_framework_of(r["group"]), []).append(r)

    out = []
    for fw, items in by_fw.items():
        agree = sum(1 for r in items if _agreement(r))
        total = len(items)
        fps = sum(1 for r in items if r["doc_id_external"] is not None and r["verdict"] != "approve")
        fns = sum(1 for r in items if r["doc_id_external"] is None and r["verdict"] == "approve")
        out.append({
            "framework": fw,
            "totalVerdicts": total,
            "agreement": agree,
            "accuracyPct": round(agree / total * 100, 1) if total else None,
            "falsePositives": fps,
            "falseNegatives": fns,
        })
    # Worst-accuracy first so admins see problem areas at the top.
    out.sort(key=lambda x: (x["accuracyPct"] if x["accuracyPct"] is not None else 100))
    return out


# ---- 4 · disagreements (the real learning opportunities) -------------------

def disagreements(db: Session, limit: int = 25) -> list[dict]:
    """Concrete rows where AI and reviewer diverged. Two flavours:

      * false_positive — AI auto-attached at high confidence, reviewer
        rejected. Often a prompt/grounding bug — the doc looked relevant
        but didn't actually establish the requirement.

      * false_negative — AI did not attach, reviewer approved (presumably
        after manually pinning a doc). Often a retrieval gap — the right
        chunks weren't fetched, or the matcher's threshold was too high.

    Newest first; capped at `limit`. The frontend deep-links each row
    into the Review screen so the admin can inspect grounding + verdict
    reason side-by-side and either tweak a prompt or just acknowledge."""
    rows = _load_verdict_rows(db)
    out = []
    for r in rows:
        ai_attached = r["doc_id_external"] is not None
        approved = r["verdict"] == "approve"
        if ai_attached and not approved:
            kind = "false_positive"
            note = (
                f"AI auto-attached at "
                f"{(r['confidence'] or 0) * 100:.0f}% — reviewer "
                f"{'rejected' if r['verdict'] == 'reject' else 'asked for more info'}."
            )
        elif not ai_attached and approved:
            kind = "false_negative"
            note = "AI didn't attach a doc — reviewer approved manually after attaching one themselves."
        else:
            continue
        out.append({
            "kind": kind,
            "requirementId": r["req_id"],
            "title": r["title"],
            "framework": _framework_of(r["group"]),
            "vendor": r["vendor"],
            "auditRunId": r["audit_id"],
            "confidence": r["confidence"],
            "verdict": r["verdict"],
            "verdictBy": r["verdict_by"],
            "verdictAt": r["verdict_at"],
            "verdictReason": r["verdict_reason"],
            "note": note,
        })
    # Newest verdict first — admin cares about recent surprises more than ancient.
    out.sort(key=lambda x: x["verdictAt"] or "", reverse=True)
    return out[:limit]


# ---- 5 · threshold suggestion ---------------------------------------------

def threshold_suggestion(db: Session, current_threshold: float) -> dict:
    """Sweep candidate auto-approve thresholds against the actual verdict
    history; pick the one that maximises F1 (balances false-positive and
    false-negative cost equally — a defensible default for compliance work
    where neither is clearly worse, though the admin can override).

    Returns: { current, suggested, currentF1, suggestedF1, expectedDelta,
               sampleSize, candidates: [...] }

    The `candidates` array lets the UI plot the F1-vs-threshold curve so
    the admin sees how peaky/flat the optimum is. A flat curve means it
    doesn't matter much; a sharp peak means the suggestion is meaningful."""
    rows = [
        r for r in _load_verdict_rows(db)
        if r["confidence"] is not None and r["verdict"] in ("approve", "reject")
    ]
    n = len(rows)
    if n < 10:
        return {
            "current": current_threshold,
            "suggested": current_threshold,
            "currentF1": None,
            "suggestedF1": None,
            "expectedDelta": None,
            "sampleSize": n,
            "candidates": [],
            "rationale": "Not enough labelled verdicts yet (need ≥10 with both confidence + reviewer verdict).",
        }

    def f1_at(t: float) -> dict:
        # At threshold `t`, the matcher would have auto-attached every
        # requirement with confidence >= t. We compare that hypothetical
        # decision against the actual reviewer verdict.
        tp = fp = fn = 0
        for r in rows:
            would_attach = r["confidence"] >= t
            approved = r["verdict"] == "approve"
            if would_attach and approved:
                tp += 1
            elif would_attach and not approved:
                fp += 1
            elif not would_attach and approved:
                fn += 1
        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
        return {"t": round(t, 2), "f1": round(f1, 3),
                "precision": round(precision, 3), "recall": round(recall, 3),
                "tp": tp, "fp": fp, "fn": fn}

    candidates = [f1_at(0.50 + i * 0.05) for i in range(11)]  # 0.50..1.00
    best = max(candidates, key=lambda c: c["f1"])
    current_eval = f1_at(current_threshold)

    return {
        "current": round(current_threshold, 2),
        "suggested": best["t"],
        "currentF1": current_eval["f1"],
        "suggestedF1": best["f1"],
        "expectedDelta": round(best["f1"] - current_eval["f1"], 3),
        "sampleSize": n,
        "candidates": candidates,
        "rationale": (
            f"Sweep across {len(candidates)} candidate thresholds (0.50–1.00 step 0.05). "
            f"F1 peaks at {best['t']:.2f} (vs current {current_threshold:.2f})."
        ),
    }
