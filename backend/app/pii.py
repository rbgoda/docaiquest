"""M44.P11 · PII detection + redaction for LLM-bound prompts.

Detects PII in text, replaces each occurrence with a stable placeholder
(e.g. `[PERSON_1]`, `[EMAIL_2]`), and gives the caller a mapping so the
LLM's response can be detokenized back to real values.

Design rationale and the threat model are in
`docs/architecture/PII_LLM_SAFETY.md`. The summary: prompts sent to
external LLM providers should not contain raw PII. We tokenize before
send, the model operates on opaque placeholders, we detokenize on
return.

The redactor runs in three tiers:

  Tier 1 · regex bank · high-confidence patterns (emails, phones, IDs)
  Tier 2 · entity-table lookup · names/orgs from our own NER
  Tier 3 · LLM-side guard · system-prompt instruction added by caller

Tier 1 is in this module. Tier 2 takes an optional `extra_terms` list
the caller assembles from the `entities` table for the current doc.
Tier 3 lives in the LLM call site (gateway.py + chat_pipeline.py).

Performance: Tier 1 + Tier 2 combined run in <5ms on a 10KB prompt.
Acceptable for compliance-grade audit pipelines.

Failure mode: never raises. If detection produces garbage, the worst
case is a placeholder that leaks back unredacted in the response;
better than a 500 on the whole request.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Final

log = logging.getLogger("docaiq.pii")


# ── Category grouping ───────────────────────────────────────────────────
# Each regex kind maps to a user-facing category group. The admin UI shows
# these groups as toggleable checkboxes — the gateway reads which groups are
# enabled and passes them as `mask_categories` to redact().

# Category → set of regex kinds it controls
_CATEGORY_KINDS: Final[dict[str, set[str]]] = {
    "dates":       {"dob", "dob_eu"},
    "names":       {"person", "org"},
    "contact":     {"email", "phone_e164", "phone_us"},
    "govt_ids":    {"nric", "aadhaar", "pan_in", "gstin_in", "ssn", "ein",
                    "uk_nino", "passport"},
    "financial":   {"credit_card", "iban", "account", "swift_bic"},
    "network":     {"ip_v4"},
}

# Category label + default (shown in admin UI)
PII_CATEGORIES: Final[dict[str, dict]] = {
    "dates":     {"label": "Dates",      "default": False,
                  "help": "DOB-style dates (YYYY-MM-DD, DD/MM/YYYY). Disable for extraction — dates are the data."},
    "names":     {"label": "Names",      "default": False,
                  "help": "Person & org names. Search-critical — redacting breaks name queries."},
    "contact":   {"label": "Contact",    "default": True,
                  "help": "Email addresses and phone numbers."},
    "govt_ids":  {"label": "Govt IDs",   "default": True,
                  "help": "NRIC, Aadhaar, PAN, SSN, EIN, passport, national insurance."},
    "financial": {"label": "Financial",  "default": True,
                  "help": "Credit cards, IBAN, bank account numbers, SWIFT/BIC."},
    "network":   {"label": "Network",    "default": True,
                  "help": "IPv4 addresses."},
}

# Flattened: kind → category for the Tier 1 regex bank + Tier 2 entities.
_KIND_TO_CATEGORY: Final[dict[str, str]] = {}
for _cat, _kinds in _CATEGORY_KINDS.items():
    for _k in _kinds:
        _KIND_TO_CATEGORY[_k] = _cat


# ── Tier 1 · regex bank ────────────────────────────────────────────────
# Order matters: more-specific patterns fire first so a 12-digit Aadhaar
# isn't shadowed by the generic credit-card pattern. Each entry is
# (kind, regex). `kind` is the placeholder family the matches get
# tokenized as.

_PATTERNS: Final[list[tuple[str, re.Pattern]]] = [
    # Credit card FIRST · 13-19 digits with optional separators ·
    # consumes long digit sequences before the 12-digit Aadhaar
    # pattern can grab their prefix. Luhn-validated below.
    ("credit_card", re.compile(r"\b(?:\d[ -]?){13,19}\b")),
    # Singapore NRIC · S/T/F/G/M + 7 digits + letter
    ("nric", re.compile(r"\b[STFGM]\d{7}[A-Z]\b")),
    # India Aadhaar · 12 digits in 4-4-4 spacing (runs AFTER credit_card
    # to avoid eating the prefix of a 16-digit card number).
    ("aadhaar", re.compile(r"\b\d{4}\s\d{4}\s\d{4}\b")),
    # India PAN · 5 letters + 4 digits + letter
    ("pan_in", re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")),
    # India GSTIN · 2 digits + PAN + checksum letter + Z + alnum
    ("gstin_in", re.compile(r"\b\d{2}[A-Z]{5}\d{4}[A-Z]\d{1}Z[A-Z\d]\b")),
    # US SSN
    ("ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    # US EIN
    ("ein", re.compile(r"\b\d{2}-\d{7}\b")),
    # UK National Insurance Number · 2 letters + 6 digits + letter
    ("uk_nino", re.compile(r"\b[A-CEGHJ-PR-TW-Z]{2}\d{6}[A-D]\b")),
    # Passport · 1 letter + 6-9 alnum (broad; refined by context)
    ("passport", re.compile(r"\b[A-Z]\d{6,9}\b")),
    # IBAN · 2 letters + 2 digits + 11-30 alnum
    ("iban", re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b")),
    # SWIFT/BIC is LABEL-ANCHORED below (_SWIFT_PATTERNS) — the bare 8-uppercase
    # pattern matched ordinary words ("EVIDENCE", "DOCUMENT") and added noise.
    # Email · RFC 5322 simple subset
    ("email", re.compile(r"\b[\w._%+-]+@[\w.-]+\.[A-Za-z]{2,}\b")),
    # E.164 phone · +CC...
    ("phone_e164", re.compile(r"\+\d{1,3}[ -]?\(?\d{1,4}\)?[ -]?\d{2,4}[ -]?\d{2,9}")),
    # US-style phone · (NNN) NNN-NNNN or NNN-NNN-NNNN
    ("phone_us", re.compile(r"\b\(?\d{3}\)?[ -]\d{3}[ -]\d{4}\b")),
    # Date of birth · YYYY-MM-DD, DD/MM/YYYY, MM/DD/YYYY (loose).
    # Tagged as `dob` so the caller knows this is potentially-sensitive
    # date data, not just any date in the doc.
    ("dob", re.compile(r"\b(?:19|20)\d{2}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])\b")),
    ("dob_eu", re.compile(r"\b(?:0[1-9]|[12]\d|3[01])/(?:0[1-9]|1[0-2])/(?:19|20)\d{2}\b")),
    # IPv4 · 4 octets 0-255
    ("ip_v4", re.compile(r"\b(?:25[0-5]|2[0-4]\d|[01]?\d\d?)(?:\.(?:25[0-5]|2[0-4]\d|[01]?\d\d?)){3}\b")),
]


@dataclass
class RedactionResult:
    """Output of `redact()`. Hold onto `mapping` if you plan to
    detokenize the LLM's response back to real values."""
    text: str
    mapping: dict[str, str] = field(default_factory=dict)
    # Per-kind count so the audit ledger can record what was removed
    # without storing the actual values.
    counts: dict[str, int] = field(default_factory=dict)


# ── Tier 1 helpers ────────────────────────────────────────────────────

def _luhn_ok(digits: str) -> bool:
    """Validate credit-card style number by Luhn checksum. Rejects
    numbers that look like 13-19 digits but aren't real cards."""
    d = [int(c) for c in digits if c.isdigit()]
    if not 13 <= len(d) <= 19:
        return False
    s = 0
    for i, x in enumerate(reversed(d)):
        if i % 2 == 1:
            x *= 2
            if x > 9:
                x -= 9
        s += x
    return s % 10 == 0


# B4 · label-anchored person-name detection (no NER dependency). We tokenize
# names that appear after a PII-bearing LABEL or an honorific — the high-risk
# cases on the medical / ID / financial docs this product handles. The vault
# mapping is doc-wide and apply_mapping propagates to EVERY occurrence, so we
# only need to detect each name once. The capture group is the name; the label
# is preserved. Conservative on purpose (label-anchored) to avoid over-redacting
# ordinary capitalized words.
_NAME_LABEL = (
    r"Name|Full[ ]Name|First[ ]Name|Last[ ]Name|Patient|Patient[ ]Name|"
    r"Holder|Card[ ]?holder|Account[ ]Holder|Policy[ ]?holder|Insured|"
    r"Beneficiary|Nominee|Applicant|Employee|Customer|Client|Guardian|"
    r"Father(?:'s[ ]Name)?|Mother(?:'s[ ]Name)?|Spouse|Signatory|Authorised[ ]Signatory"
)
# A name word, but subsequent words must NOT be a label/honorific — otherwise a
# run-together "John Smith Patient: Jane Doe" greedily absorbs "Patient".
_NAME_STOP = (
    r"Name|Full|First|Last|Patient|Holder|Card|Account|Policy|Insured|Beneficiary|"
    r"Nominee|Applicant|Employee|Customer|Client|Guardian|Father|Mother|Spouse|"
    r"Signatory|Authorised|Mr|Mrs|Ms|Miss|Dr|Prof|SSN|DOB|ID|No|Date"
)
_NAME_TOKEN = (
    rf"[A-Z][A-Za-z'’.\-]+(?:[ ]+(?!(?:{_NAME_STOP})\b)[A-Z][A-Za-z'’.\-]+){{0,3}}"
)
_NAME_PATTERNS: Final[list[re.Pattern]] = [
    re.compile(rf"(?:{_NAME_LABEL})\s*[:\-–]\s*({_NAME_TOKEN})"),
    re.compile(rf"\b(?:Mr|Mrs|Ms|Miss|Dr|Prof)\.?\s+({_NAME_TOKEN})"),
]

# B5 · label-anchored BANK ACCOUNT numbers. Generic account numbers have no
# fixed format (unlike IBAN/SWIFT), so we anchor on a money-transfer label and
# capture the digit run. Anchoring keeps invoice/PO/reference numbers ("Invoice
# number: INV-1") from being swept up — only ACCOUNT-labelled numbers match.
_ACCOUNT_PATTERNS: Final[list[re.Pattern]] = [
    re.compile(
        r"(?:bank\s+a/?c|account|acct|a/c)\s*"
        r"(?:number|no\.?|#|num)?\s*[:\-#]?\s*"
        r"([0-9][0-9\- ]{4,22}[0-9])",
        re.IGNORECASE,
    ),
]

# SWIFT/BIC · label-anchored so plain 8-letter uppercase words aren't matched.
_SWIFT_PATTERNS: Final[list[re.Pattern]] = [
    re.compile(r"(?i:swift|bic)(?:\s*code)?\s*[:\-#]?\s*"
               r"([A-Z]{6}[A-Z0-9]{2}(?:[A-Z0-9]{3})?)\b"),
]


# ── Main entry ────────────────────────────────────────────────────────

def redact(
    text: str,
    *,
    extra_terms: list[tuple[str, str]] | None = None,
    placeholder_seed: int = 1,
    redact_names: bool = True,
    mask_categories: set[str] | None = None,
) -> RedactionResult:
    """Redact PII from `text`. Returns the modified text + a mapping
    from placeholder → original.

    `extra_terms` is the Tier 2 list: each tuple is `(kind, value)`,
    where `kind` is a family like 'person' or 'org' and `value` is
    the literal string to redact. Caller assembles this from the
    `entities` table for the current document. Tier 2 runs AFTER
    Tier 1 so regex matches take priority for ambiguous tokens.

    `placeholder_seed` lets the caller stitch multiple redactions
    together with non-overlapping indices.

    `mask_categories` is the set of category groups to redact (e.g.
    {"dates", "names", "contact"}). When None (default), ALL categories
    are redacted. Pass an empty set to skip all redaction.
    """
    if not text:
        return RedactionResult(text=text)

    # Resolve which regex kinds to actually run. None = all.
    _active_kinds: set[str] | None = None
    if mask_categories is not None:
        _active_kinds = set()
        for cat in mask_categories:
            _active_kinds |= _CATEGORY_KINDS.get(cat, set())

    result = text
    mapping: dict[str, str] = {}
    counts: dict[str, int] = {}
    next_index: dict[str, int] = {}
    seen_vals: dict[str, str] = {}  # value → placeholder (stable within a call)

    def _next(kind: str) -> str:
        idx = next_index.get(kind, placeholder_seed)
        next_index[kind] = idx + 1
        return f"[{kind.upper()}_{idx}]"

    def _tok(kind: str, value: str) -> str:
        """Placeholder for `value`, REUSED if the same value was already seen in
        this call — so a value gets one stable token everywhere it appears (e.g.
        in both the evidence and the answer the grounding guard compares)."""
        if value in seen_vals:
            return seen_vals[value]
        ph = _next(kind)
        seen_vals[value] = ph
        mapping[ph] = value
        counts[kind] = counts.get(kind, 0) + 1
        return ph

    # Tier 1 · regex bank
    for kind, pattern in _PATTERNS:
        # Skip if this kind's category isn't in the active set
        if _active_kinds is not None and _KIND_TO_CATEGORY.get(kind) not in _active_kinds:
            continue
        def _sub(m: re.Match) -> str:
            value = m.group(0)
            # Validate credit cards with Luhn · skip false positives
            if kind == "credit_card" and not _luhn_ok(value):
                return value
            # Skip passport regex collision with e.g. "REQ-027"
            if kind == "passport" and "-" in value:
                return value
            return _tok(kind, value)
        result = pattern.sub(_sub, result)

    # Tier 1.5 · B4 · label-anchored person names. Replace only the captured
    # name (group 1), preserving the label. Each detected name is added to the
    # mapping; the doc-wide vault + apply_mapping then propagate it everywhere.
    _names_active = _active_kinds is None or "names" in _active_kinds
    if redact_names and _names_active:
        for pattern in _NAME_PATTERNS:
            def _sub_name(m: re.Match) -> str:
                name = m.group(1)
                if not name or len(name) < 3:
                    return m.group(0)
                return m.group(0).replace(name, _tok("person", name), 1)
            result = pattern.sub(_sub_name, result)

    # Tier 1.6 · B5 · label-anchored bank account numbers. Replace only the
    # captured digit run (group 1), preserving the "Account number:" label.
    _financial_active = _active_kinds is None or "financial" in _active_kinds
    if _financial_active:
        for pattern in _ACCOUNT_PATTERNS:
            def _sub_acct(m: re.Match) -> str:
                val = m.group(1)
                if not val or sum(c.isdigit() for c in val) < 5:
                    return m.group(0)
                return m.group(0).replace(val, _tok("account", val), 1)
            result = pattern.sub(_sub_acct, result)

    # Tier 1.7 · label-anchored SWIFT/BIC (replace only the captured code).
    if _financial_active:
        for pattern in _SWIFT_PATTERNS:
            def _sub_swift(m: re.Match) -> str:
                return m.group(0).replace(m.group(1), _tok("swift_bic", m.group(1)), 1)
            result = pattern.sub(_sub_swift, result)

    # Tier 2 · entity-table lookup
    if extra_terms:
        # Sort by length descending so 'Smart Audit Pte Ltd' is replaced
        # before its substring 'Smart Audit'.
        sorted_terms = sorted(extra_terms, key=lambda t: -len(t[1]))
        for kind, value in sorted_terms:
            if not value or len(value) < 3:
                continue
            # Names are the search key — skip when name redaction is off,
            # or when the names category isn't active.
            if kind in ("person", "org"):
                if not redact_names or not _names_active:
                    continue
            # Skip if this entity kind's category isn't active
            if _active_kinds is not None and _KIND_TO_CATEGORY.get(kind) not in _active_kinds:
                continue
            # Word-boundary match so 'Goda' doesn't match inside 'Pagoda'
            pattern = re.compile(rf"\b{re.escape(value)}\b", re.IGNORECASE)
            def _sub(m: re.Match, k=kind) -> str:
                return _tok(k, m.group(0))
            result = pattern.sub(_sub, result)

    return RedactionResult(text=result, mapping=mapping, counts=counts)


def detokenize(text: str, mapping: dict[str, str]) -> str:
    """Replace placeholders in the LLM's response with the originals.
    Order doesn't matter · placeholders are unique. Missing keys
    (e.g. the LLM invented a new [PERSON_99]) are left as-is."""
    if not text or not mapping:
        return text
    out = text
    for ph, original in mapping.items():
        out = out.replace(ph, original)
    return out


# ── Helpers for system-prompt augmentation ─────────────────────────────

PRESERVE_PLACEHOLDERS_INSTRUCTION: Final[str] = (
    "PRIVACY: The input may contain tokens like [PERSON_1], [EMAIL_2], "
    "[NRIC_1] etc. Treat each token AS IF it were the real value it stands "
    "for — use it verbatim wherever you'd state that value. Do NOT guess the "
    "original, do NOT substitute, and do NOT annotate it (never add words like "
    "'(redacted)', '(masked)', or '(hidden)' next to a token)."
)


def fingerprint(text: str) -> str:
    """SHA-256 of UTF-8 bytes · used for audit-ledger hashes so we can
    prove what was sent without retaining the contents."""
    import hashlib
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()
