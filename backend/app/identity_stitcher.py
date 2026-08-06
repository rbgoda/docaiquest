"""Identity stitcher — groups KYC records into deduplicated Subjects.

When a new kyc_records row lands with extracted fields, this module
finds an existing KycSubject in the same tenant that matches, or
creates a new one. Matching strategy:

  Individuals:
    1. Extract (name, dob) from the record's fields. If either is
       missing/empty, mark as pending (no stitching yet).
    2. Look for an existing subject with EXACT-match DOB in the same
       tenant.
    3. Among those, find one whose canonical_name has SequenceMatcher
       similarity ≥ 0.85 (handles "John Smith" ↔ "John A. Smith" etc).
    4. If found, link the record + bump doc_count + update coverage.
       Else create a new subject.

  Business (KYB):
    1. Extract `registration_number` or `tax_id_number` from the fields.
    2. Exact match on either field within tenant.
    3. If found, link. Else create new subject (subject_kind=business).

Status update (called after each link):
  pending — any time canonical_name or DOB is missing
  partial — identity established, but < N% of expected KYC requirements
            have linked records
  verified — ≥ 80% of expected requirements covered

`requirement_coverage` is a {req_id_external: doc_id_external} map — the
Subjects view renders this as a per-subject grid without re-joining.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.orm import Document, KycRecord, KycSubject, Requirement

log = logging.getLogger(__name__)


# Confidence thresholds for stitching. The name match is the loose one;
# DOB / registration-number are exact.
_NAME_SIM_THRESHOLD = 0.85
# When this fraction of the tenant's KYC-* requirements are covered for
# a subject, flip status to 'verified'. Below it, 'partial' (provided
# the identity is established at all).
_VERIFIED_COVERAGE_RATIO = 0.80


def _normalize_name(name: str | None) -> str:
    if not name:
        return ""
    return " ".join(name.strip().lower().split())


def _name_similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, _normalize_name(a), _normalize_name(b)).ratio()


def _extract_individual_identity(fields: dict[str, Any]) -> tuple[str, str]:
    """Return (name, dob) from a kyc_record's fields. Empty string for
    either if not present."""
    name = (
        fields.get("holder_name")
        or fields.get("account_holder")
        or ""
    ).strip()
    dob = (fields.get("dob") or "").strip()
    return name, dob


def _extract_business_identity(fields: dict[str, Any]) -> tuple[str, str]:
    """Return (company_name, registration_or_tax_number)."""
    name = (fields.get("company_legal_name") or fields.get("company_or_holder_name") or "").strip()
    reg = (fields.get("registration_number") or fields.get("tax_id_number") or "").strip()
    return name, reg


# Doc-types that represent a business entity rather than an individual.
_BUSINESS_DOC_TYPES = {"incorporation_cert", "tax_id"}


def _kyc_requirement_count(db: Session, tenant_id: str) -> int:
    """How many KYC-* + KYB-* + AML-SOF-* requirements does this tenant
    have loaded? Used as the denominator for coverage %."""
    rows = db.scalars(
        select(Requirement.id_external).where(Requirement.tenant_id == tenant_id)
    ).all()
    return sum(
        1 for r in rows
        if r.startswith(("KYC-", "KYB-", "AML-SOF-"))
    )


def stitch(db: Session, record: KycRecord, requirement_id_external: str | None) -> KycSubject | None:
    """Find or create the KycSubject for this record, link, and update
    status. Returns the subject (or None if identity couldn't be
    established — record stays unstitched, status reads 'pending')."""
    tenant_id = record.tenant_id
    fields = record.fields or {}
    is_business = record.doc_type in _BUSINESS_DOC_TYPES

    if is_business:
        name, reg = _extract_business_identity(fields)
        if not name and not reg:
            log.info("stitcher: business record %s has no identity fields; leaving unstitched", record.pk)
            return None
        # Exact registration number match first
        subject = None
        if reg:
            candidates = db.scalars(
                select(KycSubject).where(
                    KycSubject.tenant_id == tenant_id,
                    KycSubject.subject_kind == "business",
                    KycSubject.canonical_dob == reg,  # reuse the DOB column for biz reg number
                )
            ).all()
            subject = candidates[0] if candidates else None
        if subject is None and name:
            # Fallback to name-only fuzzy match for business
            all_biz = db.scalars(
                select(KycSubject).where(
                    KycSubject.tenant_id == tenant_id,
                    KycSubject.subject_kind == "business",
                )
            ).all()
            best, best_sim = None, 0.0
            for cand in all_biz:
                sim = _name_similarity(cand.canonical_name, name)
                if sim > best_sim:
                    best, best_sim = cand, sim
            if best_sim >= _NAME_SIM_THRESHOLD:
                subject = best
        if subject is None:
            subject = KycSubject(
                tenant_id=tenant_id,
                canonical_name=name or "(unknown business)",
                canonical_dob=reg or None,
                subject_kind="business",
                status="pending",
            )
            db.add(subject)
            db.flush()
            log.info("stitcher: created business subject pk=%s name=%r reg=%r", subject.pk, name, reg)
    else:
        name, dob = _extract_individual_identity(fields)
        if not name and not dob:
            log.info("stitcher: individual record %s has no identity; leaving unstitched", record.pk)
            return None
        subject = None
        if dob:
            # Same-DOB candidates first
            same_dob = db.scalars(
                select(KycSubject).where(
                    KycSubject.tenant_id == tenant_id,
                    KycSubject.subject_kind == "individual",
                    KycSubject.canonical_dob == dob,
                )
            ).all()
            if name and same_dob:
                # Fuzzy name match within the DOB cohort
                best, best_sim = None, 0.0
                for cand in same_dob:
                    sim = _name_similarity(cand.canonical_name, name)
                    if sim > best_sim:
                        best, best_sim = cand, sim
                if best_sim >= _NAME_SIM_THRESHOLD:
                    subject = best
            elif same_dob and not name:
                # No name to match against; take the first DOB candidate
                subject = same_dob[0]
        if subject is None and name:
            # No DOB match — try name-only (looser; could mis-merge homonyms,
            # but reviewer can override later via subject merge/split).
            no_dob = db.scalars(
                select(KycSubject).where(
                    KycSubject.tenant_id == tenant_id,
                    KycSubject.subject_kind == "individual",
                    KycSubject.canonical_dob.is_(None),
                )
            ).all()
            for cand in no_dob:
                if _name_similarity(cand.canonical_name, name) >= _NAME_SIM_THRESHOLD:
                    subject = cand
                    break
        if subject is None:
            subject = KycSubject(
                tenant_id=tenant_id,
                canonical_name=name or "(unknown)",
                canonical_dob=dob or None,
                subject_kind="individual",
                status="pending",
            )
            db.add(subject)
            db.flush()
            log.info("stitcher: created individual subject pk=%s name=%r dob=%r", subject.pk, name, dob)

    # Link the record to the subject
    record.subject_pk = subject.pk

    # Update the subject's rollup
    subject.doc_count = (subject.doc_count or 0) + 1
    coverage = dict(subject.requirement_coverage or {})
    if requirement_id_external:
        # Look up the doc's id_external (we have document_pk on the record)
        doc_ext = db.scalar(
            select(Document.id_external).where(Document.pk == record.document_pk)
        )
        if doc_ext:
            coverage[requirement_id_external] = doc_ext
    subject.requirement_coverage = coverage

    # Status update
    total_kyc_reqs = _kyc_requirement_count(db, tenant_id)
    identity_ok = bool(
        subject.canonical_name and subject.canonical_name != "(unknown)" and subject.canonical_name != "(unknown business)"
    )
    if not identity_ok:
        subject.status = "pending"
    else:
        coverage_ratio = (len(coverage) / total_kyc_reqs) if total_kyc_reqs > 0 else 0.0
        if coverage_ratio >= _VERIFIED_COVERAGE_RATIO:
            subject.status = "verified"
        else:
            subject.status = "partial"

    subject.updated_at = datetime.now(timezone.utc)
    db.flush()
    log.info(
        "stitcher: linked record pk=%s → subject pk=%s; status=%s, doc_count=%d, coverage=%d/%d",
        record.pk, subject.pk, subject.status, subject.doc_count, len(coverage), total_kyc_reqs,
    )
    return subject
