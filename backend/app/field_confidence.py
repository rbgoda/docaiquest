"""Per-field extraction confidence (Reducto-parity G4).

Pure-stdlib (no DB/LLM) so it unit-tests offline. The extractor reports one
whole-doc confidence (`_doc_confidence`); this derives a deterministic 0..1
score PER extracted field from cheap, trustworthy signals so the review UI (G7)
can surface exactly the fields worth a human glance:

  * empty value            → 0.0 (nothing extracted)
  * grounded (a tight bbox was located for the field) → boost
  * value plausibility vs the field name (a "date" field that looks like a
    date, an "amount" that has a number, an "email" that is an email) →
    boost / penalty
  * whole-doc confidence as the prior

This is deliberately conservative: it can only *lower* trust on a clearly
wrong-looking value and *raise* it on a grounded, well-formed one. It does NOT
ask the LLM for per-field confidence (token cost + noisy self-reports); that
remains a possible future refinement.
"""
from __future__ import annotations

import re
from typing import Any

LOW_CONFIDENCE = 0.6  # fields below this are review candidates (feeds G7)

_DATE_RE = re.compile(
    r"(\d{1,4}[-/.\s]\d{1,2}[-/.\s]\d{1,4})"
    r"|((jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{1,2})",
    re.I,
)
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_HAS_DIGIT = re.compile(r"\d")

_DATE_HINTS = ("date", "expiry", "expire", "expiration", "issued", "issue", "dob",
               "valid", "born", "renewal", "effective")
_MONEY_HINTS = ("amount", "total", "price", "due", "balance", "sum", "paid",
                "premium", "salary", "fee", "cost", "value", "limit", "coverage")
_EMAIL_HINTS = ("email", "e-mail")
_PERCENT_HINTS = ("percent", "rate", "apr", "interest")


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _expected_kind(name: str) -> str | None:
    n = name.lower()
    if any(h in n for h in _EMAIL_HINTS):
        return "email"
    if any(h in n for h in _DATE_HINTS):
        return "date"
    if any(h in n for h in _PERCENT_HINTS):
        return "percent"
    if any(h in n for h in _MONEY_HINTS):
        return "money"
    return None


def _matches_kind(value: str, kind: str) -> bool:
    v = value.strip()
    if kind == "email":
        return bool(_EMAIL_RE.match(v))
    if kind == "date":
        return bool(_DATE_RE.search(v))
    if kind == "percent":
        return bool(_HAS_DIGIT.search(v)) and ("%" in v or "percent" in v.lower())
    if kind == "money":
        return bool(_HAS_DIGIT.search(v))
    return True


def score_one(name: str, value: Any, *, grounded: bool, base: float) -> float:
    """Confidence for a single field value."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return 0.0
    if isinstance(value, (list, dict)):
        # Composite (records / nested) — not directly verifiable here; trust the
        # doc prior when populated, 0 when empty.
        return base if value else 0.0
    s = base
    if grounded:
        s += 0.15
    kind = _expected_kind(name)
    if kind is not None:
        s += 0.1 if _matches_kind(str(value), kind) else -0.3
    return _clamp(s)


def is_empty(value) -> bool:
    """Empty / not-applicable field value — no content to be confident about."""
    return (value is None or (isinstance(value, str) and not value.strip())
            or (isinstance(value, (list, dict)) and not value))


def score_fields(fields: dict | None, field_bboxes: dict | None = None,
                 doc_confidence: float | None = None) -> dict[str, float]:
    """Per-field confidence map. `field_bboxes` keys mark grounded fields;
    `doc_confidence` is the prior (defaults to 0.7 when unknown).

    Empty / not-applicable fields are OMITTED, not scored 0.0 — they aren't failed
    extractions (e.g. a generic-envelope field like `primary_amount` on a resume), and
    scoring them 0.0 wrongly drags confidence means and flags them as errors in review."""
    base = 0.7 if doc_confidence is None else _clamp(float(doc_confidence))
    bb = field_bboxes or {}
    out: dict[str, float] = {}
    for name, value in (fields or {}).items():
        if is_empty(value):
            continue
        out[name] = round(score_one(name, value, grounded=name in bb, base=base), 3)
    return out


def prune_empty(extracted_fields):
    """Return a copy of an `extracted_fields` blob with empty/not-applicable fields removed
    from `fields` and `field_confidence`. An empty field carries no information and (as a
    generic-envelope field on a doc type where it doesn't apply, e.g. `issuer_address` on a
    resume) only clutters the schema and drags the confidence mean. Non-destructive —
    for display/serialization; the stored blob is untouched."""
    if not isinstance(extracted_fields, dict):
        return extracted_fields
    fields = extracted_fields.get("fields")
    if not isinstance(fields, dict):
        return extracted_fields
    kept = {k: v for k, v in fields.items() if not is_empty(v)}
    if len(kept) == len(fields):
        return extracted_fields
    out = dict(extracted_fields)
    out["fields"] = kept
    fc = extracted_fields.get("field_confidence")
    if isinstance(fc, dict):
        out["field_confidence"] = {k: v for k, v in fc.items() if k in kept}
    return out


def low_confidence_fields(scores: dict[str, float], threshold: float = LOW_CONFIDENCE,
                          *, include_missing: bool = False) -> list[str]:
    """The review queue for G7: fields that were **extracted but are uncertain**
    (0 < score < threshold).

    Missing fields (score 0.0 — nothing was extracted) are EXCLUDED by default:
    they're absent, not wrong, and the review UI should not nag about empty
    optional fields. Pass `include_missing=True` to also surface them (e.g. to
    flag missing *required* fields as a separate concern).
    """
    out: list[str] = []
    for k, v in (scores or {}).items():
        if v >= threshold:
            continue
        if v == 0.0 and not include_missing:
            continue
        out.append(k)
    return out
