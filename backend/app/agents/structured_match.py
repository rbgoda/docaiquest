"""Structured precheck for the matcher (M28.9).

Before the matcher's retrieval + LLM validator runs against a (document,
requirement) pair, this module applies hard-constraint checks against the
document's `extracted_fields`. If a constraint fails (Singapore passport
attached to an AU passport requirement; Stripe payout attached to a tax
requirement), we short-circuit with confidence=0.0 and skip the LLM call.

The check works by token-matching the requirement's title / subtitle /
required_docs against known **discriminator categories**:

  - country / jurisdiction (Singapore, Australia, India, USA, UK, Canada, …)
  - currency (SGD, AUD, INR, USD, GBP, …)
  - document subtype within a doc_type (passport vs national ID vs driver licence)
  - time period (year mentioned in requirement vs date in document)

If the requirement explicitly mentions a discriminator value, the document
MUST carry the same value in its extracted fields. Otherwise → REJECT.

Learning loop integration: every match_rejected entry in document_reviews
(M28.8) becomes future training data — patterns like (doc_type=passport,
requirement.country=AU, doc.country=SG) → hard reject can be added here
without touching the matcher core.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


# ── Country / jurisdiction discriminators ────────────────────────────────
#
# Maps tokens-as-they-appear-in-requirement-text to canonical country
# codes. Bidirectional: we also normalize doc-side country fields.

COUNTRY_TOKENS: dict[str, set[str]] = {
    "SG": {"singapore", "singaporean", "sg", "republic of singapore", "sgp"},
    "AU": {"australia", "australian", "aus", "au", "commonwealth of australia"},
    "IN": {"india", "indian", "ind", "republic of india", "bharat"},
    "US": {"usa", "united states", "us", "american", "u.s.", "u.s.a."},
    "UK": {"uk", "united kingdom", "british", "england", "great britain", "gb", "gbr"},
    "CA": {"canada", "canadian", "can"},
    "NZ": {"new zealand", "nz", "kiwi"},
    "MY": {"malaysia", "malaysian", "my", "mys"},
    "ID": {"indonesia", "indonesian", "idn"},
    "PH": {"philippines", "filipino", "phl", "ph"},
    "TH": {"thailand", "thai", "tha"},
    "VN": {"vietnam", "vietnamese", "vnm"},
    "JP": {"japan", "japanese", "jpn"},
    "CN": {"china", "chinese", "chn", "people's republic of china", "prc"},
    "HK": {"hong kong", "hkg"},
    "DE": {"germany", "german", "deu"},
    "FR": {"france", "french", "fra"},
    "ES": {"spain", "spanish", "esp"},
    "IT": {"italy", "italian", "ita"},
    "NL": {"netherlands", "dutch", "nld", "holland"},
}

# Reverse index: token → country code, for fast lookup.
_TOKEN_TO_COUNTRY: dict[str, str] = {}
for cc, tokens in COUNTRY_TOKENS.items():
    for t in tokens:
        _TOKEN_TO_COUNTRY[t.lower()] = cc

# Currency codes that imply jurisdiction (loose signal, not hard reject).
CURRENCY_TO_COUNTRY: dict[str, str] = {
    "SGD": "SG", "S$": "SG",
    "AUD": "AU", "A$": "AU",
    "INR": "IN", "₹": "IN", "Rs": "IN",
    "USD": "US", "$": "US",  # ambiguous but common default
    "GBP": "UK", "£": "UK",
    "CAD": "CA", "C$": "CA",
    "EUR": "EU", "€": "EU",
    "JPY": "JP", "¥": "JP",
    "CNY": "CN", "HKD": "HK",
    "MYR": "MY", "RM": "MY",
    "IDR": "ID", "Rp": "ID",
    "PHP": "PH", "₱": "PH",
    "THB": "TH", "฿": "TH",
}


@dataclass
class StructuredVerdict:
    """Result of the structured precheck. `pass_` False means the matcher
    should skip the LLM and record the rejection with the given reason."""
    pass_: bool
    reason: str | None = None
    constraint: str | None = None  # which kind of mismatch (country, currency, …)


def _lower_words(text: str | None) -> set[str]:
    if not text:
        return set()
    # Tokenize on non-alphanumeric, lowercase, keep multi-word phrases too.
    return set(re.findall(r"[a-zA-Z][a-zA-Z']+", text.lower()))


def _countries_in_text(text: str) -> set[str]:
    """Find all country codes mentioned in a chunk of free text. Catches
    both single-word ('singapore') and multi-word ('united kingdom') forms."""
    if not text:
        return set()
    lower = text.lower()
    found: set[str] = set()
    # Multi-word phrases first (longest first to avoid greedy matches)
    multi_phrases = sorted(
        ((t, cc) for t, cc in _TOKEN_TO_COUNTRY.items() if " " in t),
        key=lambda x: -len(x[0]),
    )
    for phrase, cc in multi_phrases:
        if phrase in lower:
            found.add(cc)
    # Then single-word with word boundaries
    words = re.findall(r"[a-zA-Z][a-zA-Z']+", lower)
    for w in words:
        if w in _TOKEN_TO_COUNTRY:
            found.add(_TOKEN_TO_COUNTRY[w])
    return found


def _doc_country(fields: dict | None) -> str | None:
    """Best-effort extraction of the document's own country / jurisdiction
    from its extracted_fields. Checks multiple field names + does substring
    search inside each value so "SINGAPORE CITIZEN" or "Republic of India"
    still resolves correctly."""
    if not fields:
        return None
    # Direct country / nationality / issuing-authority fields.
    for key in ("country", "nationality", "issuing_country", "issuing_authority",
                "country_of_issue", "jurisdiction", "country_address",
                "address_country", "issued_in"):
        v = fields.get(key)
        if not isinstance(v, str) or not v.strip():
            continue
        lower = v.lower().strip()
        # 1. Exact-token match (cheap).
        cc = _TOKEN_TO_COUNTRY.get(lower)
        if cc:
            return cc
        # 2. ISO code straight from the extractor (e.g. "SG").
        up = v.strip().upper()
        if up in COUNTRY_TOKENS:
            return up
        # 3. Substring scan — catches "SINGAPORE CITIZEN", "Republic of India",
        #    "Commonwealth of Australia", "Government of India" etc.
        matches = _countries_in_text(v)
        if len(matches) == 1:
            return next(iter(matches))
        # Multiple country mentions in one field → ambiguous, fall through to next key.
    # Currency-derived (weaker signal — only use when explicit country missing).
    cur = fields.get("currency")
    if isinstance(cur, str):
        cur_cc = CURRENCY_TO_COUNTRY.get(cur.strip().upper()) or CURRENCY_TO_COUNTRY.get(cur.strip())
        if cur_cc and cur_cc != "EU":  # EUR is too ambiguous for a hard signal
            return cur_cc
    return None


# ── Semantic compatibility · doc_type × requirement family ────────────────
#
# This is the big one. Pure retrieval matches "receipt with text mentioning
# tax" to a "Tax ID / EIN" requirement — wrong. A receipt is NEVER valid
# evidence for KYC identity, KYB business identification, or AML risk
# screening, no matter what the LLM thinks.
#
# Two-step rule:
#   1. Detect the requirement family from its `group` field and title tokens.
#   2. Check if the doc's classified type is in the family's allowed set.
#      If not → hard reject.
#
# Tunable: when the doc has no classified type, skip the check (be liberal).
# When the requirement family isn't recognised, skip (don't constrain
# requirements we don't know about — let the LLM decide).

# Token signals that identify a requirement family (case-insensitive substring).
# Order matters · MORE SPECIFIC families come first so they win the match.
# A req group of "KYC / KYB / AML_custom · Identity (generic)" used to false-
# trigger kyc_identity for EVERY KYC requirement (because both "kyc" and
# "identity" were in the signal list). That's why an address-proof req
# accepted a passport as evidence — the family was wrong from the start.
_FAMILY_SIGNALS: list[tuple[str, list[str]]] = [
    # (family_name, list of substrings that mean "this is a <family> requirement")
    # Selfie · stricter than general identity — only selfie/live-capture sat.
    ("kyc_selfie",       ["selfie", "live capture", "live-capture",
                          "selfie matching", "liveness check"]),
    # Address · before kyc_identity because address-of-identity reqs would
    # otherwise be swallowed by the broader identity matcher.
    ("kyc_address",      ["proof of address", "proof of current address",
                          "residential address", "utility bill",
                          "current address", "billing address",
                          "address proof", "address verification"]),
    # Date of birth · narrower than identity in general; allowed doc types
    # overlap with kyc_identity but separating them makes future tuning easier.
    ("kyc_dob",          ["date of birth", "dob on file", "birth date",
                          "proof of age", "age verification"]),
    # Identity proof · removed broad "kyc" and "identity" tokens that
    # false-triggered on the framework group prefix. Now only matches on
    # specific identity-doc cues.
    ("kyc_identity",     ["photo id", "passport", "national id", "national id card",
                          "driver licence", "driver license", "driving licence",
                          "nric", "fin card", "ssn", "social security",
                          "cpf", "voter id", "government-issued photo",
                          "government issued photo", "identity document"]),
    ("kyb_business",     ["kyb", "incorporation", "tax id", "ein", "vat registration",
                          "business address", "operating licence", "operating license",
                          "registered business", "company registration"]),
    ("aml_risk",         ["aml", "pep declaration", "adverse media",
                          "sanctions screening", "politically exposed",
                          "ubo declaration", "beneficial ownership"]),
    ("financial_audit",  ["audited financial statement", "financial report",
                          "income statement", "balance sheet", "cash flow"]),
    ("expense_audit",    ["expense report", "receipts roll-up", "expense substantiation",
                          "expense category", "travel expense"]),
    ("compliance_cert",  ["iso 27001", "soc 2", "hipaa", "pci dss", "gdpr",
                          "compliance certificate", "type ii report"]),
    ("policy_doc",       ["policy", "procedure", "code of conduct", "acceptable use"]),
    ("access_control",   ["mfa", "access review", "user access", "privileged access",
                          "logical access", "rbac"]),
    ("insurance",        ["insurance certificate", "cyber insurance", "liability insurance"]),
]

# For each family, the set of doc_types that CAN satisfy it. Anything outside
# this set is a hard reject.
_FAMILY_ALLOWED_DOC_TYPES: dict[str, set[str]] = {
    "kyc_identity":     {"passport", "national_id_card", "national_id",
                         "aadhar", "aadhar_card", "aadhaar", "aadhaar_card",
                         "pan_card", "pan",
                         "driver_licence", "driving_licence", "driver_license",
                         "voter_id", "voter_id_card",
                         "photo_id", "identity_document",
                         "residence_permit", "social_security_card"},
    # Selfie-only · the photo-of-the-holder side of KYC. A passport scan
    # does NOT satisfy this — the requirement is for a *fresh* photo
    # captured at onboarding to compare against the ID doc's photo.
    "kyc_selfie":       {"selfie", "live_capture", "liveness_check",
                         "live_photo", "onboarding_photo"},
    # Date-of-birth · any government-issued doc that carries DOB is fine.
    # Includes birth certificates, which kyc_identity (intentionally) doesn't.
    "kyc_dob":          {"passport", "national_id_card", "national_id",
                         "aadhar", "aadhar_card", "aadhaar", "aadhaar_card",
                         "pan_card", "pan", "voter_id", "voter_id_card",
                         "driver_licence", "driving_licence", "driver_license",
                         "birth_certificate", "identity_document"},
    "kyc_address":      {"utility_bill", "bank_statement", "credit_card_statement",
                         "government_letter", "address_proof", "lease_agreement"},
    "kyb_business":     {"incorporation_certificate", "tax_certificate", "vat_certificate",
                         "business_registration", "operating_licence",
                         "company_registration", "articles_of_association"},
    "aml_risk":         {"pep_declaration", "ubo_declaration", "aml_attestation",
                         "sanctions_screening_report", "adverse_media_report"},
    "financial_audit":  {"audited_financial_statement", "financial_statement",
                         "income_statement", "balance_sheet", "bank_statement",
                         "credit_card_statement"},
    "expense_audit":    {"receipt", "expense_claim", "sales_receipt",
                         "bank_statement", "credit_card_statement", "invoice"},
    "compliance_cert":  {"iso_cert", "soc2_report", "compliance_certificate",
                         "audit_report", "type_ii_report"},
    "policy_doc":       {"policy", "policy_document", "procedure",
                         "code_of_conduct", "acceptable_use_policy"},
    "access_control":   {"access_review", "policy", "mfa_policy", "access_report",
                         "iam_export", "log_export"},
    "insurance":        {"insurance_certificate", "insurance_policy", "coi"},
}


def _detect_requirement_family(requirement_text: str, requirement_group: str | None) -> str | None:
    """Return the requirement family slug or None if unrecognised. Checks
    both the group field (most reliable) and free-text title for the
    signals listed in _FAMILY_SIGNALS."""
    haystack_parts = [(requirement_text or "").lower()]
    if requirement_group:
        haystack_parts.append(requirement_group.lower())
    haystack = " ".join(haystack_parts)
    for family, signals in _FAMILY_SIGNALS:
        for s in signals:
            if s in haystack:
                return family
    return None


def _normalize_name(s: str) -> str:
    """Lowercased, whitespace-collapsed, punctuation-stripped — for
    case/spacing tolerant comparison of person names."""
    return re.sub(r"[^a-z0-9 ]+", "", (s or "").lower()).strip()


def _doc_subject_names(doc_fields: dict) -> list[str]:
    """Extract the person-name(s) the document is for.

    KYC extractors land the name under one of several schemas:
      · Flat:   name / full_name / holder_name / applicant_name / subject_name
      · Split:  first_name + middle_name + last_name (or surname / given_name)
      · MRZ:    holder_name parsed from passport machine-readable zone

    Returns lower-cased normalised name strings. Empty list when nothing
    is found.

    M31.2.1 fix: previously only flat-name fields were checked, so docs
    extracted by the passport schema (which uses first/middle/last) sailed
    past the subject precheck silently. That's why Kalyani Goda Rajesh's
    passport reached the LLM unchecked on the test123 audit for Rajesh.
    """
    out: list[str] = []
    # Flat-name fields
    for key in ("name", "full_name", "holder_name", "applicant_name",
                "given_name", "subject_name", "account_holder"):
        v = doc_fields.get(key)
        if isinstance(v, str) and v.strip():
            out.append(_normalize_name(v))
    # Split-name fields — compose into one string.
    parts = []
    for key in ("first_name", "middle_name", "last_name", "surname"):
        v = doc_fields.get(key)
        if isinstance(v, str) and v.strip():
            parts.append(v.strip())
    if parts:
        composed = _normalize_name(" ".join(parts))
        if composed:
            out.append(composed)
    return [n for n in out if n]


def check(
    requirement_text: str,
    doc_fields: dict | None,
    doc_type: str | None,
    requirement_group: str | None = None,
    subjects: list[str] | None = None,
    doc_person_names: list[str] | None = None,
) -> StructuredVerdict:
    """Run the structured precheck.

    `requirement_text` should be the concatenation of title + subtitle +
    required_docs (whatever the matcher already builds as its retrieval
    query). We re-tokenize it here for discriminator detection — keeps
    this module independent of the matcher's query construction.

    `doc_fields` is the `extracted_fields.fields` dict (the inner schema
    payload, not the wrapper with confidence / model). Pass None if the
    extractor hasn't run yet — in that case some checks are skipped, but
    the requirement-family check still runs because it only needs doc_type.

    `requirement_group` (e.g. "KYC · Identity (generic)") is used to detect
    the requirement family. When None, falls back to scanning requirement_text.

    Returns StructuredVerdict(pass_=True) when no mismatch is detected.
    Returns pass_=False with a human-readable reason when there's a hard
    constraint violation.
    """
    # ── Requirement-family vs doc-type compatibility check ───────────
    # Runs FIRST because it's the most aggressive false-positive killer.
    # A sales_receipt attached to a KYC identity requirement is wrong
    # regardless of any other field. Bias: when we know the family and
    # the doc has a type, enforce strictly. Otherwise let it pass.
    if doc_type:
        family = _detect_requirement_family(requirement_text, requirement_group)
        if family is not None:
            allowed = _FAMILY_ALLOWED_DOC_TYPES.get(family, set())
            if allowed and doc_type.lower() not in {t.lower() for t in allowed}:
                return StructuredVerdict(
                    pass_=False,
                    constraint="family",
                    reason=(
                        f"Doc-type incompatible with requirement family · "
                        f"requirement is '{family}' (expects one of {sorted(list(allowed))[:4]}…), "
                        f"document is '{doc_type}'. Not valid evidence."
                    ),
                )

    if not doc_fields:
        # Without extracted fields we can't do country / period checks.
        # The family check above already ran; everything else passes.
        return StructuredVerdict(pass_=True)

    # ── Country / jurisdiction check ──────────────────────────────────
    req_countries = _countries_in_text(requirement_text)
    if req_countries:
        doc_country = _doc_country(doc_fields)
        if doc_country and doc_country not in req_countries:
            # The requirement explicitly names a country (or set of
            # countries) AND the document carries a different one. This
            # is the classic "Singapore passport attached to AU req" case.
            return StructuredVerdict(
                pass_=False,
                constraint="country",
                reason=(
                    f"Country mismatch · requirement mentions "
                    f"{sorted(req_countries)}, document is from {doc_country}. "
                    "Cross-jurisdiction evidence not auto-attached."
                ),
            )

    # ── Year / period check ───────────────────────────────────────────
    # If the requirement mentions a year (e.g. "Q1 2026 access review") AND
    # the document carries an issue / period date, they must overlap.
    req_years = set(re.findall(r"\b(20\d{2})\b", requirement_text))
    if req_years:
        doc_year = None
        for key in ("date", "issue_date", "period_start", "period_end",
                    "fiscal_year", "year", "reporting_period"):
            v = doc_fields.get(key)
            if isinstance(v, str):
                m = re.search(r"\b(20\d{2})\b", v)
                if m:
                    doc_year = m.group(1)
                    break
        if doc_year and doc_year not in req_years:
            return StructuredVerdict(
                pass_=False,
                constraint="period",
                reason=(
                    f"Period mismatch · requirement mentions year "
                    f"{sorted(req_years)}, document is dated {doc_year}."
                ),
            )

    # ── Subject-name check (M31.2 — KYC subject binding) ─────────────
    # When the audit has named subjects (Rajesh Goda, John Doe, ...) the
    # doc's extracted name MUST match one of them. Without this, a
    # passport for "Alice Smith" gets accepted as evidence of Rajesh's
    # identity. Case + whitespace + punctuation tolerant equality; partial
    # last-name match (e.g. "Goda, Rajesh" vs "Rajesh Goda") via token
    # set overlap.
    #
    # M31.2.3 · Graph fallback. If extracted_fields has no name (Aadhar
    # extractor returned {}, but graph bootstrap extracted Person entity
    # 'Rajesh Balvantrai Goda'), use those graph person names as the
    # doc's subject names. Caller passes them as doc_person_names.
    if subjects:
        doc_names = _doc_subject_names(doc_fields) if doc_fields else []
        for n in (doc_person_names or []):
            if isinstance(n, str) and n.strip():
                doc_names.append(_normalize_name(n))
        if doc_names:
            req_names = {_normalize_name(s) for s in subjects if s}
            matched = False
            for dn in doc_names:
                if dn in req_names:
                    matched = True
                    break
                # Token-set match: "Rajesh Goda" ~ "Goda Rajesh", or
                # "Rajesh Kumar Goda" ~ "Rajesh Goda" (subset).
                dn_toks = set(dn.split())
                for rn in req_names:
                    rn_toks = set(rn.split())
                    if rn_toks and (rn_toks <= dn_toks or dn_toks <= rn_toks):
                        matched = True
                        break
                if matched:
                    break
            if not matched:
                return StructuredVerdict(
                    pass_=False,
                    constraint="subject_name",
                    reason=(
                        f"Subject mismatch · this audit is for "
                        f"{sorted(subjects)[:3]}, document is for "
                        f"{[n.title() for n in doc_names][:2]}. Not valid evidence."
                    ),
                )

    # ── Doc-type/subtype check ────────────────────────────────────────
    # When the requirement asks specifically for "passport" but the
    # classifier tagged the doc as "national_id_card" (or similar narrow
    # subtype), reject. Loose check — only fires on a small set of common
    # confusable subtypes.
    SUBTYPE_PAIRS = [
        ({"passport"},        {"national_id_card", "driver_licence", "voter_id"}),
        ({"bank_statement"},  {"credit_card_statement"}),
    ]
    if doc_type:
        req_words = _lower_words(requirement_text)
        for required, conflicting in SUBTYPE_PAIRS:
            if (required & req_words) and (doc_type.lower() in {c.lower() for c in conflicting}):
                return StructuredVerdict(
                    pass_=False,
                    constraint="doc_subtype",
                    reason=(
                        f"Doc-type mismatch · requirement asks for "
                        f"{sorted(required)[0]}, doc classified as {doc_type}."
                    ),
                )

    return StructuredVerdict(pass_=True)
