"""Regex-based entity extraction.

Targeted at compliance text — pulls the references auditors actually navigate
by (control IDs, monetary limits, expiry dates, framework versions).

Intentionally narrow: regex is fast, deterministic, and trivially debuggable.
The cost is false positives ("$5 footnote" gets tagged as money) and missed
context. M9 swaps this for an LLM-driven NER step that understands semantics.

Stays a pure function — input string, output list of Entity dataclasses.
Ingestion calls this per chunk and writes rows.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Patterns. Each emits (kind, normalized text, optional metadata).
_USD_MONEY = re.compile(
    r"\b(?:USD\s*)?\$?\s*(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s*(?:USD|million|M|K|billion|B)?\b",
    re.IGNORECASE,
)
_ISO_STANDARD = re.compile(r"\bISO/IEC\s+(\d{4,5})(?::(\d{4}))?\b")
_DATE_NAMED = re.compile(
    r"\b(\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{4})\b",
)
_DATE_NUMERIC = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
_CONTROL_ID = re.compile(
    r"\b(?:CC\d+(?:\.\d+)?|REQ-\d+|A\.\d+(?:\.\d+)?(?:\.\d+)?|HIPAA|PCI-DSS(?:\s*v\d+)?|NIST\s+\d+-\d+|SOC\s*2(?:\s+Type\s*II)?|GDPR|CMMC)\b",
    re.IGNORECASE,
)
_PERCENT = re.compile(r"\b(\d{1,3}(?:\.\d+)?)\s*%")
_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")


@dataclass(frozen=True)
class ExtractedEntity:
    kind: str
    text: str
    metadata: dict | None = field(default=None)


def extract_entities(text: str) -> list[ExtractedEntity]:
    """Return a deduped list of entities from `text`. Dedup is by (kind, text).
    The caller adds page/chunk context — this function stays text-only."""
    seen: set[tuple[str, str]] = set()
    out: list[ExtractedEntity] = []

    def _emit(kind: str, value: str, meta: dict | None = None) -> None:
        key = (kind, value.strip())
        if key[1] and key not in seen:
            seen.add(key)
            out.append(ExtractedEntity(kind=kind, text=key[1], metadata=meta))

    # Money — capture the matched substring so " USD 10,000,000 " renders cleanly.
    for m in _USD_MONEY.finditer(text):
        raw = m.group(0).strip()
        # Filter out pathological matches like bare years ("2026") or single digits
        # in non-money context. Heuristic: require a $ / USD / million / M / K marker.
        if not any(tok in raw.lower() for tok in ("$", "usd", "million", "billion")):
            if not raw.lower().endswith(("m", "k", "b")):
                continue
        _emit("money", raw)

    for m in _ISO_STANDARD.finditer(text):
        _emit("standard", m.group(0), {"family": "ISO/IEC", "number": m.group(1), "year": m.group(2)})

    for m in _DATE_NAMED.finditer(text):
        _emit("date", m.group(0))
    for m in _DATE_NUMERIC.finditer(text):
        _emit("date", m.group(0), {"format": "iso"})

    for m in _CONTROL_ID.finditer(text):
        _emit("control_id", m.group(0).upper().replace("  ", " "))

    for m in _PERCENT.finditer(text):
        _emit("percent", m.group(0))

    for m in _EMAIL.finditer(text):
        _emit("email", m.group(0))

    return out
