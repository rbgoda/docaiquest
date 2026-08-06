"""Recall-gap detector — the "what did the extractor MISS?" signal.

Schema-driven extraction pulls the fields it knows to look for. This scans the parsed text
for **structured-looking spans** (dates, money, emails, phones, IDs/codes, percentages) that
**no extracted field covers**, and surfaces them as candidate *missed* fields the reviewer
can locate on the page and add (via the region→field endpoint).

Pure-stdlib + heuristic (regex + a coverage check) — deliberately conservative and offline-
testable. It can only ever SUGGEST a review; false positives are cheap (the reviewer ignores
them), false negatives just mean we didn't surface something.
"""
from __future__ import annotations

import re

_WS = re.compile(r"\s+")
_NONALNUM = re.compile(r"[^a-z0-9]+")

# Ordered so more specific kinds win when spans overlap (email before id, money before id).
_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("email", re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")),
    ("money", re.compile(r"(?:USD|SGD|EUR|GBP|INR|RM|Rs\.?|US\$|S\$|\$|€|£|₹)\s?\d[\d,]*(?:\.\d{1,2})?", re.I)),
    ("percent", re.compile(r"\b\d{1,3}(?:\.\d+)?\s?%")),
    ("date", re.compile(
        r"\b(?:\d{1,2}[/\-.]\d{1,2}[/\-.]\d{2,4}"
        r"|\d{4}[/\-.]\d{1,2}[/\-.]\d{1,2}"
        r"|\d{1,2}\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{2,4}"
        r"|(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4})\b", re.I)),
    ("phone", re.compile(r"(?:\+?\d[\d\-\s]{7,}\d)")),
    ("id", re.compile(r"\b(?=[A-Z0-9\-]*[A-Z])(?=[A-Z0-9\-]*\d)[A-Z0-9]{2,}(?:-?[A-Z0-9]{2,}){0,3}\b")),
]


def _norm(s: str) -> str:
    return _NONALNUM.sub("", (s or "").lower())


def find_gaps(text: str, covered_values, *, limit: int = 40) -> list[dict]:
    """Return candidate missed values: structured-looking spans in `text` not covered by
    any extracted field value in `covered_values`. Each: {kind, value}."""
    text = text or ""
    covered = [_norm(v) for v in covered_values if isinstance(v, str) and v.strip()]
    covered = [c for c in covered if len(c) >= 3]
    gaps: list[dict] = []
    seen: set[str] = set()
    for kind, pat in _PATTERNS:
        for m in pat.finditer(text):
            val = (m.group(0) or "").strip().strip(".,;:")
            n = _norm(val)
            if len(n) < 3 or n in seen:
                continue
            # "id" is noisy — require a digit and at least 4 alphanumerics to qualify.
            if kind == "id" and (len(n) < 4 or not any(ch.isdigit() for ch in n)):
                continue
            if kind == "phone" and sum(ch.isdigit() for ch in val) < 8:
                continue
            # Covered if this value is a substring of (or contains) any extracted field value.
            if any(n in c or c in n for c in covered):
                continue
            seen.add(n)
            gaps.append({"kind": kind, "value": val})
            if len(gaps) >= limit:
                return gaps
    return gaps


def collect_covered_values(extracted_fields) -> list[str]:
    """Flatten every scalar value the extractor already captured — scalars + string leaves
    inside array/object fields — so find_gaps knows what's already covered."""
    out: list[str] = []
    if not isinstance(extracted_fields, dict):
        return out
    fields = extracted_fields.get("fields")
    if not isinstance(fields, dict):
        return out

    def _walk(v):
        if isinstance(v, str):
            out.append(v)
        elif isinstance(v, (int, float)):
            out.append(str(v))
        elif isinstance(v, dict):
            for x in v.values():
                _walk(x)
        elif isinstance(v, list):
            for x in v:
                _walk(x)

    _walk(fields)
    return out
