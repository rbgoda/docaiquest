"""M31 cleanup · prune audit_run_requirements rows where the requirement
no longer matches the audit's framework set under the fixed
`_matches_framework` (M31-1, underscore guard).

Background: before M31-1, the backend matcher used raw token overlap. A
custom pack named `KYC_custom` would token-match the original `KYC` pack
because both shared the `kyc` token, so creating an audit on `KYC_custom`
attached BOTH packs' requirements (36 + 4 = 40 instead of 4). The function
was fixed in `app/repositories/audit_runs.py` but already-created audits
still carry the bogus join rows in `audit_run_requirements`.

This script re-evaluates every row against the corrected matcher and
prunes mismatches. Idempotent — safe to re-run.

Behavior:
  - For each AuditRunRequirement (req_pk, audit_pk):
      · Look up the Requirement.group + AuditRun.frameworks/framework.
      · Run `_matches_any_framework(req.group, frameworks)`.
      · If False → delete the join row. Also bump audit counters down.
  - If the row had a verdict (approve/reject/needs-info) set, log it
    before pruning so the reviewer's work isn't silently dropped.
  - At end: print per-audit before/after totals so operator can verify.

Usage (inside backend container):

    DOCAIQ_TENANT_ID=test-audit-tech python3 scripts/prune_mismatched_audit_reqs.py

    # dry-run mode shows what WOULD be pruned without writing
    DOCAIQ_TENANT_ID=test-audit-tech python3 scripts/prune_mismatched_audit_reqs.py --dry-run
"""

from __future__ import annotations

import os
import sys
from collections import defaultdict

from sqlalchemy import select, delete

sys.path.insert(0, "/app")

from app.db import SessionLocal, current_tenant
from app.orm import AuditRun, AuditRunRequirement, Requirement
from app.repositories.audit_runs import _matches_any_framework


def prune_tenant(db, tenant_id: str, dry_run: bool = False) -> dict:
    current_tenant.set(tenant_id)

    audits = db.scalars(
        select(AuditRun).where(AuditRun.tenant_id == tenant_id)
    ).all()
    if not audits:
        print(f"[{tenant_id}] no audits — nothing to prune.")
        return {"audits_scanned": 0, "rows_pruned": 0, "verdicts_lost": 0}

    audits_by_pk = {a.pk: a for a in audits}

    join_rows = db.scalars(
        select(AuditRunRequirement).where(
            AuditRunRequirement.tenant_id == tenant_id,
        )
    ).all()

    req_pks = {arr.requirement_pk for arr in join_rows}
    reqs = db.scalars(
        select(Requirement).where(
            Requirement.tenant_id == tenant_id,
            Requirement.pk.in_(req_pks),
        )
    ).all() if req_pks else []
    reqs_by_pk = {r.pk: r for r in reqs}

    per_audit_before = defaultdict(int)
    per_audit_pruned = defaultdict(int)
    per_audit_verdicts_lost = defaultdict(int)
    to_prune: list[AuditRunRequirement] = []

    for arr in join_rows:
        per_audit_before[arr.audit_run_pk] += 1
        audit = audits_by_pk.get(arr.audit_run_pk)
        req = reqs_by_pk.get(arr.requirement_pk)
        if audit is None or req is None:
            # Dangling — prune it.
            to_prune.append(arr)
            per_audit_pruned[arr.audit_run_pk] += 1
            continue
        fws = (audit.frameworks or []) or ([audit.framework] if audit.framework else [])
        if not _matches_any_framework(req.group or "", fws):
            to_prune.append(arr)
            per_audit_pruned[arr.audit_run_pk] += 1
            if arr.verdict:
                per_audit_verdicts_lost[arr.audit_run_pk] += 1

    print(f"\n=== Tenant {tenant_id} ===")
    for audit in audits:
        before = per_audit_before.get(audit.pk, 0)
        pruned = per_audit_pruned.get(audit.pk, 0)
        verdicts_lost = per_audit_verdicts_lost.get(audit.pk, 0)
        if pruned == 0:
            continue
        after = before - pruned
        fw_label = (
            ", ".join(audit.frameworks)
            if audit.frameworks else (audit.framework or "—")
        )
        verdict_note = f" (·{verdicts_lost} had verdicts)" if verdicts_lost else ""
        print(
            f"  [{audit.id_external}] {audit.vendor} · {fw_label} ·"
            f" {before} → {after} reqs (pruned {pruned}{verdict_note})"
        )

    total_pruned = len(to_prune)
    total_verdicts_lost = sum(per_audit_verdicts_lost.values())
    if total_pruned == 0:
        print(f"  → nothing to prune.")
        return {"audits_scanned": len(audits), "rows_pruned": 0, "verdicts_lost": 0}

    if dry_run:
        print(f"\n[dry-run] would prune {total_pruned} rows · {total_verdicts_lost} verdicts.")
        return {
            "audits_scanned": len(audits),
            "rows_pruned": total_pruned,
            "verdicts_lost": total_verdicts_lost,
        }

    # Bulk-delete + update each audit's counters.
    prune_pks = [arr.pk for arr in to_prune]
    db.execute(delete(AuditRunRequirement).where(AuditRunRequirement.pk.in_(prune_pks)))
    # Decrement audit totals/pending in line with what we pruned.
    for audit in audits:
        pruned_n = per_audit_pruned.get(audit.pk, 0)
        if pruned_n == 0:
            continue
        # Reduce total by however many were pruned. Pending falls by the
        # subset that had no verdict; verdicted rows fall out of compliant/
        # review/missing depending on their verdict — but counters are
        # derived elsewhere (see `_derive_counters`) so simpler to leave
        # those re-derived at next read. Just adjust total + pending to
        # keep the row internally consistent.
        new_total = max(0, (audit.total or 0) - pruned_n)
        # Approximate pending drop: pruned minus the verdict-lost portion.
        verdict_lost = per_audit_verdicts_lost.get(audit.pk, 0)
        non_verdict_pruned = pruned_n - verdict_lost
        new_pending = max(0, (audit.pending or 0) - non_verdict_pruned)
        audit.total = new_total
        audit.pending = new_pending

    db.commit()
    print(f"\nDone · pruned {total_pruned} rows across {len([a for a in audits if per_audit_pruned.get(a.pk)])} audits.")
    if total_verdicts_lost:
        print(f"WARNING · {total_verdicts_lost} pruned rows had reviewer verdicts. Those audits may need review.")
    return {
        "audits_scanned": len(audits),
        "rows_pruned": total_pruned,
        "verdicts_lost": total_verdicts_lost,
    }


def main() -> int:
    dry_run = "--dry-run" in sys.argv
    tenant = os.environ.get("DOCAIQ_TENANT_ID")
    if not tenant:
        print("Set DOCAIQ_TENANT_ID env var.", file=sys.stderr)
        return 2

    with SessionLocal() as db:
        prune_tenant(db, tenant, dry_run=dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
