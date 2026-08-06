"""Canonical-form helpers — normalize raw text so dedup + fuzzy lookups
work across docs that spell the same thing slightly differently.

  "Mr. Goda Rajesh Balvantrai"  ┐
  "Goda Rajesh Balvantrai"      ├──► "goda rajesh balvantrai"
  "GODA RAJESH BALVANTRAI"      ┘

  "S$1,420.00"  → "1420.00 SGD"
  "$ 1,420"     → "1420.00 USD"
  "2026-05-12"  ─── (already canonical)
  "12 May 2026" → "2026-05-12"
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Optional


_TITLE_RX = re.compile(r"^(mr|mrs|ms|miss|dr|prof|sir|madam|mx|mr\.|mrs\.|ms\.|dr\.|prof\.)\s+", re.IGNORECASE)
_WS_RX = re.compile(r"\s+")
# PII placeholders the extractor may emit ([NRIC_1], [PERSON_1], [nric_2]) — never a real name.
_PII_TOKEN_RX = re.compile(r"\[[a-z][a-z_]*\d*\]", re.IGNORECASE)
# Garbage tokens that slip through as "names" (nulls, OCR crumbs, generic words).
_NAME_JUNK = {"na", "n/a", "none", "null", "nil", "unknown", "tbd", "not applicable",
              "category", "ategory", "name", "self", "applicant", "holder", "customer"}


def _valid_name(out: str) -> bool:
    """A cleaned canonical is a plausible person/org name — not a placeholder, number, id,
    OCR crumb, or descriptive blob. Rejecting here keeps junk out of the entity graph."""
    if not out or len(out) < 2:
        return False
    if not re.search(r"[a-z]", out):            # must have letters
        return False
    if re.fullmatch(r"[\d\W_]+", out):          # digits / symbols only
        return False
    if re.fullmatch(r"[a-z]?\d[\d\W a-z]*", out) and len(re.findall(r"[a-z]{2,}", out)) < 2:
        return False                            # id-ish: '36150737', '248-l j c a'
    if len(out.split()) > 7:                    # descriptive text, not a name
        return False
    if out in _NAME_JUNK:
        return False
    return True


def canon_name(s: str | None) -> str:
    """Canonical form for a person/org name. Returns "" for anything that isn't a plausible
    name (placeholder, pure number/id, OCR crumb, descriptive blob) — so the caller creates
    no entity for it."""
    if not s:
        return ""
    out = _PII_TOKEN_RX.sub(" ", s)             # drop [NRIC_1]-style placeholders
    out = out.strip().lower()
    out = _TITLE_RX.sub("", out)
    out = _WS_RX.sub(" ", out)                  # collapse whitespace incl. embedded newlines
    out = re.sub(r"\([^)]*\)\s*$", "", out).strip()
    out = re.sub(r"^[^\w]+", "", out)           # strip leading noise ('. kalyani …')
    out = re.sub(r"[.,;:]+$", "", out).strip()
    return out if _valid_name(out) else ""


def canon_name_sorted(s: str | None) -> str:
    """Like ``canon_name()`` but token-sorts the result so word-order
    variants collapse to the same key at write time::

        "Rajesh Goda"  → "goda rajesh"
        "Goda Rajesh"  → "goda rajesh"

    Used for person entities only (never orgs or locations — sorting
    "Smart Audit Pte Ltd" would break org dedup).
    """
    c = canon_name(s)
    if not c:
        return ""
    tokens = c.split()
    tokens.sort()
    return " ".join(tokens)


def canon_org(s: str | None) -> str:
    """Canonical form for an organisation name. Same as canon_name plus
    common corporate-suffix dropping for better cross-doc dedup."""
    base = canon_name(s)
    if not base:
        return ""
    base = re.sub(r"\s+(pte ltd|pvt ltd|ltd|inc|llc|gmbh|sa|bv|pte|plc|corp|co)\.?$", "", base)
    return base.strip()


# ── Multi-person splitting — joint holders / co-applicants ──────────────
# Detects separators that join two distinct person names (joint accounts,
# co-signers, etc.) and splits them so each name gets its own Entity row.
# Conservative: each part must independently pass _valid_name() AND have
# 2+ words (plausible first+last name), so "J. R. R. Tolkien" and
# "Jack and Jill Party Supplies" are never split.

_MULTI_PERSON_SEPS = [
    # Compound separators MUST come before their simpler components
    # so "and/or" does not get split by the "/" inside it.
    re.compile(r"\s+and/or\s+", re.IGNORECASE),      # "Rajesh and/or Kalyani"
    re.compile(r"\s*&?/OR\s*", re.IGNORECASE),       # "[PERSON_1] &/OR KALYANIGODA RAJESH"
    re.compile(r"\s*/\s*"),                          # "GODA / KALYANI"
    re.compile(r"\s*&\s*"),                          # "Rajesh & Kalyani"
    re.compile(r"\s+and\s+", re.IGNORECASE),         # "Rajesh and Kalyani"
    # NOTE: comma/semicolon deliberately excluded — org names like
    # "BARCLAYS BANK, IRELAND PLC FRANKFURT BRANCH" contain commas as
    # branch/location separators, not person separators.
]


def split_multi_person(s: str | None) -> list[str]:
    """Split a joint-holder / co-applicant string into individual person names.

    ``"GODA RAJESH BALVANTRAI / KALYANI GODA RAJESH"``
        → ``["GODA RAJESH BALVANTRAI", "KALYANI GODA RAJESH"]``
    ``"Rajesh Goda & Kalyani Goda"``
        → ``["Rajesh Goda", "Kalyani Goda"]``

    Each part must independently pass ``_valid_name()`` and contain ≥2 words
    (plausible first + last name), so ``"J. R. R. Tolkien"``, ``"Anderson"``,
    and ``"Jack and Jill Party Supplies"`` are never split. If no separator
    produces a valid split, returns the original string as a single-element list.
    """
    if not s or not s.strip():
        return []
    raw = s.strip()
    for sep in _MULTI_PERSON_SEPS:
        parts = sep.split(raw)
        if len(parts) < 2:
            continue
        cleaned: list[str] = []
        ok = True
        for p in parts:
            p = p.strip()
            if not p:
                ok = False
                break
            # Skip PII placeholders like [PERSON_1], [NRIC_2] — they're not
            # real names but shouldn't block splitting the other parts.
            if _PII_TOKEN_RX.fullmatch(p):
                continue
            # Each part must be a plausible standalone person name:
            # ≥2 words (first + last) and passes _valid_name on its canonical.
            if len(p.split()) < 2:
                ok = False
                break
            c = canon_name(p)
            if not c:
                ok = False
                break
            cleaned.append(p)
        if ok and cleaned:
            return cleaned
    return [raw]


_CURRENCY_HINTS = {
    "$": "USD",
    "us$": "USD", "usd": "USD",
    "s$": "SGD", "sgd": "SGD",
    "₹": "INR", "rs": "INR", "inr": "INR",
    "€": "EUR", "eur": "EUR",
    "£": "GBP", "gbp": "GBP",
    "¥": "JPY", "jpy": "JPY",
}


def canon_money(s: str | None) -> tuple[Optional[float], Optional[str]]:
    """Parse "S$1,420.00" → (1420.00, 'SGD'). Returns (None, None) on fail."""
    if not s:
        return None, None
    raw = s.strip()
    # Detect currency by prefix or suffix token
    currency = None
    lower = raw.lower()
    # Prefix matching first (longest token wins so 's$' beats '$')
    for hint in sorted(_CURRENCY_HINTS.keys(), key=len, reverse=True):
        if lower.startswith(hint):
            currency = _CURRENCY_HINTS[hint]
            raw = raw[len(hint):].strip()
            break
    if currency is None:
        # Try suffix
        for hint, code in _CURRENCY_HINTS.items():
            if lower.endswith(hint):
                currency = code
                raw = raw[: -len(hint)].strip()
                break
    # Strip commas + extract first numeric run
    raw = raw.replace(",", "").replace(" ", "")
    m = re.search(r"-?\d+(\.\d+)?", raw)
    if not m:
        return None, currency
    try:
        return float(m.group(0)), currency
    except ValueError:
        return None, currency


def money_canonical(s: str | None) -> str:
    """String form used as the `canonical` column on Money entities.
    Two money values are considered the same entity when this matches."""
    amt, cur = canon_money(s)
    if amt is None:
        return ""
    cur = cur or "?"
    return f"{amt:.2f} {cur}"


_DATE_FORMATS = [
    "%Y-%m-%d",
    "%d-%b-%Y",
    "%d %b %Y",
    "%d %B %Y",
    "%d-%m-%Y",
    "%m/%d/%Y",
    "%d/%m/%Y",
    "%b %d, %Y",
    "%B %d, %Y",
    "%d-%B-%Y",
]


def canon_date(s: str | None) -> str:
    """Parse a date string into ISO YYYY-MM-DD form. Returns empty on fail."""
    if not s:
        return ""
    raw = s.strip().strip("[]")
    # Cheap fast-path: already ISO?
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        return raw
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return ""
