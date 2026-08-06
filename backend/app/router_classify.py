"""Doc-type → candidate-requirement router (M11.6).

Maps a classified `doc_type` (e.g. 'passport', 'pen_test_or_vuln_scan') to
the subset of requirements in the current tenant that could plausibly be
satisfied by that doc-type. The targeted matcher then walks only this
subset, skipping ~95% of LLM calls the old broad-matcher made.

Two routing signals, combined:

  1. **Hardcoded direct mapping** — for unambiguous KYC / KYB / AML
     requirements where the matching control is built into the framework
     pack (KYC-IN-01 = aadhaar, KYC-ID-02 = utility_bill, etc.). This is
     the same dict the KYC extractor uses, flipped + extended.

  2. **required_docs token overlap** — for compliance frameworks
     (SOC 2, ISO 27001, HIPAA, etc.) the framework-pack CSV already
     declares each requirement's acceptable evidence labels (e.g.
     "Quarterly access review report | Minimum-necessary access
     policy"). We tokenise both sides and find overlaps. Self-maintaining:
     adding a new framework pack with curated required_docs labels
     auto-routes new doc types without code changes.

Caller passes `doc_type` (top-1 from the classifier). Optionally also
passes alternatives — the router unions candidates across the top-3 so
near-ties don't lose recall.
"""

from __future__ import annotations

import logging
import re
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_current_tenant
from app.orm import Requirement

log = logging.getLogger(__name__)


# ── Direct doc_type → requirement_id_external prefixes / exact IDs ────────
# Used when the framework-pack author has explicitly modelled the doc-type.
# Format: doc_type → list of (exact_id_externals AND/OR prefix_patterns).
# Prefix pattern ends with '*' (e.g. 'KYC-IN-*' matches KYC-IN-01, KYC-IN-02).
_DIRECT_MAP: dict[str, list[str]] = {
    "passport":                    ["KYC-ID-01", "KYC-ID-04", "KYC-US-01", "KYC-UK-01", "KYC-EU-01", "KYC-AU-01", "KYC-CA-01"],
    "national_id":                 ["KYC-ID-01", "KYC-ID-04", "KYC-EU-01", "KYC-IN-01", "KYC-CN-01", "KYC-SG-01", "KYC-BR-01", "KYC-JP-01"],
    "driver_licence":              ["KYC-ID-01", "KYC-US-01", "KYC-UK-01", "KYC-AU-01", "KYC-CA-01", "KYC-JP-01"],
    "utility_bill":                ["KYC-ID-02"],
    "selfie_or_liveness":          ["KYC-ID-03"],
    "bank_statement":              ["AML-SOF-01", "KYC-ID-02"],
    "tax_document":                ["AML-SOF-04", "KYB-BIZ-03", "KYC-US-02"],
    # KYB
    "incorporation_certificate":   ["KYB-BIZ-01"],
    "articles_of_association":     ["KYB-BIZ-02"],
    "tax_registration":            ["KYB-BIZ-03"],
    "operating_licence":           ["KYB-BIZ-05"],
    "beneficial_ownership_declaration": ["KYB-UBO-01"],
    "shareholder_register":        ["KYB-UBO-02"],
    "board_resolution":            ["KYB-UBO-03"],
    "authorized_signatory_list":   ["KYB-UBO-04"],
    "org_chart":                   ["KYB-UBO-05"],
    # AML
    "pep_declaration":             ["AML-RISK-01"],
    "sanctions_screening_report":  ["AML-RISK-02"],
    "adverse_media_report":        ["AML-RISK-03"],
    "payslip":                     ["AML-SOF-03"],
    "audited_financial_statement": ["AML-SOF-05"],
}


# ── Token-overlap heuristic for compliance requirements ──────────────────
# Used when no direct map entry hits OR as a complement. Matches a
# classified doc_type against each requirement's `required_docs` cell:
# tokens overlap, requirement is a candidate.

_STOP_TOKENS = {"the", "a", "an", "of", "for", "to", "in", "on", "and", "or", "with", "this", "that"}


def _tokens(s: str) -> set[str]:
    if not s:
        return set()
    raw = re.findall(r"[a-z0-9]+", s.lower())
    return {t for t in raw if len(t) >= 3 and t not in _STOP_TOKENS}


def _doc_type_tokens(doc_type: str) -> set[str]:
    # 'pen_test_or_vuln_scan' → {'pen', 'test', 'vuln', 'scan'}
    return _tokens(doc_type.replace("_", " "))


def _required_docs_tokens(required_docs: list[str] | None) -> set[str]:
    if not required_docs:
        return set()
    out: set[str] = set()
    for label in required_docs:
        out |= _tokens(label)
    return out


def _matches_prefix(req_id: str, pattern: str) -> bool:
    if pattern.endswith("*"):
        return req_id.startswith(pattern[:-1])
    return req_id == pattern


def candidates(db: Session, doc_types: Iterable[str]) -> list[Requirement]:
    """Return Requirements in the current tenant that could plausibly be
    satisfied by any of the given doc_types. Union across the top-3 guesses
    so near-ties don't lose recall.

    Returns a list of ORM rows so the caller can pass them to
    match_document's candidate-subset arg.
    """
    tid = get_current_tenant()
    all_reqs = db.scalars(
        select(Requirement)
        .where(Requirement.tenant_id == tid)
        .order_by(Requirement.pk)
    ).all()
    if not all_reqs:
        return []

    matched_pks: set[int] = set()
    direct_target_ids: set[str] = set()
    type_token_bag: set[str] = set()

    for dt in doc_types:
        if not dt:
            continue
        for patt in _DIRECT_MAP.get(dt, []):
            direct_target_ids.add(patt)
        type_token_bag |= _doc_type_tokens(dt)

    for req in all_reqs:
        # 1. Direct map (exact or prefix*) → instant candidate
        for patt in direct_target_ids:
            if _matches_prefix(req.id_external, patt):
                matched_pks.add(req.pk)
                break
        else:
            # 2. Token overlap on required_docs labels — only when the
            #    direct map didn't fire for this row.
            req_tokens = _required_docs_tokens(req.required_docs)
            if req_tokens and (req_tokens & type_token_bag):
                matched_pks.add(req.pk)

    matched = [r for r in all_reqs if r.pk in matched_pks]
    log.info(
        "router: doc_types=%s matched %d/%d candidate requirements",
        list(doc_types), len(matched), len(all_reqs),
    )
    return matched
