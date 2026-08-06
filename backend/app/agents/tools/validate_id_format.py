"""Tool · validate_id_format · structural ID-format check.

The Critic catches *semantic* wrongness (LLM hint says "Aadhaar Enrolment ≠
Aadhaar"). This tool gives the agent the same knowledge as a deterministic
verifier it can call BEFORE finalising an answer.

If `expected=true` for the asked ID type but the value matches a different
known format, the response includes `mismatch_hint` so the agent can self-
correct without a critic round-trip.
"""
from __future__ import annotations

import re

from sqlalchemy.orm import Session

NAME = "validate_id_format"
DESCRIPTION = (
    "Check whether a candidate value matches the expected ID format. "
    "Knows Aadhaar / Aadhaar Enrolment / PAN / NRIC / UEN / Passport / SSN / "
    "EIN / DUNS / GSTIN / IBAN. Returns matching format + mismatch_hint when "
    "the value matches a different ID type than expected."
)
PARAMS_SCHEMA = {
    "type": "object",
    "properties": {
        "value": {"type": "string", "description": "The candidate ID value to validate."},
        "id_type": {"type": "string", "description": "Expected ID type (aadhaar|aadhaar_enrolment|pan|nric|uen|passport|ssn|ein|duns|gstin|iban|any)."},
    },
    "required": ["value"],
}


# (name, regex, human description) — order matters; more specific first
_PATTERNS: list[tuple[str, str, str]] = [
    ("aadhaar",            r"^\s*\d{4}\s+\d{4}\s+\d{4}\s*$",          "Aadhaar (India) · 12 digits, NNNN NNNN NNNN"),
    ("aadhaar_enrolment",  r"^\s*\d{4}/\d{5}/\d{5}\s*$",              "Aadhaar Enrolment · 14 digits, NNNN/NNNNN/NNNNN"),
    ("gstin",              r"^\s*[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][0-9A-Z]Z[0-9A-Z]\s*$", "GSTIN (India) · 15 chars"),
    ("pan",                r"^\s*[A-Z]{5}[0-9]{4}[A-Z]\s*$",          "PAN (India) · 10 chars AAAAA9999A"),
    ("nric",               r"^\s*[STFGM]\d{7}[A-Z]\s*$",              "NRIC (Singapore)"),
    ("uen",                r"^\s*\d{8,10}[A-Z]?\s*$",                 "UEN (Singapore business)"),
    ("ssn",                r"^\s*\d{3}-\d{2}-\d{4}\s*$",              "SSN (US)"),
    ("ein",                r"^\s*\d{2}-\d{7}\s*$",                    "EIN (US business)"),
    ("duns",               r"^\s*\d{9}\s*$",                          "DUNS · 9 digits"),
    ("iban",               r"^\s*[A-Z]{2}\d{2}[A-Z0-9]{11,30}\s*$",   "IBAN · 2 letters + 2 digits + up to 30 alnum"),
    ("passport",           r"^\s*[A-Z0-9]{5,12}\s*$",                  "Passport · 5-12 alnum chars (multi-country)"),
]


def call(*, db: Session, tenant_id: str, doc_id: str, value: str, id_type: str = "any", **_: object) -> dict:
    v = (value or "").strip()
    if not v:
        return {"valid": False, "error": "empty value"}

    matches: list[str] = []
    for name, rx, _desc in _PATTERNS:
        if re.match(rx, v):
            matches.append(name)

    expected = (id_type or "any").lower()
    if expected == "any":
        return {
            "valid": bool(matches),
            "value": v,
            "matched_formats": matches,
        }

    if expected in matches:
        return {"valid": True, "value": v, "id_type": expected, "matched_formats": matches}

    # No match — give the agent a useful "you might be looking at" hint
    return {
        "valid": False,
        "value": v,
        "id_type": expected,
        "matched_formats": matches,
        "mismatch_hint": (
            f"value matches {matches} but expected {expected} — likely wrong field"
            if matches else
            f"value does not match {expected} pattern (no known format matched either)"
        ),
    }
