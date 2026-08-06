"""Per-document extraction-coverage audit — deterministic, no LLM.

Answers, honestly, "were all the important values extracted?" by comparing the
document's VERBATIM text (its indexed chunks) against the STRUCTURED extraction (the
envelope). Two layers:

  1. Lossless capture — every salient value printed on the page lives in the indexed
     chunks, so search / RAG / chat can always find it regardless of structuring. The
     salient tokens are drawn FROM the chunk text, so this is a guarantee, not a guess.
  2. Structured coverage — how many of those salient values the structured extraction
     also mapped into typed fields, with an explicit list of any it missed.

'Salient' = decimal numbers (measurements / amounts) and dates: the high-signal,
verifiable tokens. Reference thresholds ('DESIRABLE LEVEL < 5.20') are classified out
so the number isn't inflated by false 'misses', and dates are compared after
normalization so 26-Apr-2021 (page) matches 2021-04-26 (envelope). Names/entities are
out of scope for v1 (not deterministically verifiable) — noted, not silently dropped.

Powers GET /api/documents/{id}/coverage and the JSON tab's coverage badge.
"""
from __future__ import annotations

import json
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.orm import Document, DocumentChunk

_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}

# A measurement / amount: 3.20, 1,234.56 (thousands), 5.5. Integers are excluded — they're
# mostly OCR clock-times, ids and page numbers (high noise, low value).
_DECIMAL = re.compile(r"\b\d{1,3}(?:,\d{3})+(?:\.\d+)?\b|\b\d+\.\d+\b")

# Dates in the shapes seen across docs.
_DATE_PATTERNS = [
    (re.compile(r"\b(\d{1,2})[-/ ]([A-Za-z]{3,9})[-/ ](\d{4})\b"), "dmy_name"),   # 26-Apr-2021
    (re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b"), "iso"),                       # 2021-04-26
    (re.compile(r"\b(\d{1,2})[-/](\d{1,2})[-/](\d{2,4})\b"), "dmy_num"),           # 26/04/2021
    (re.compile(r"\b([A-Za-z]{3,9})\.?\s+(\d{1,2}),?\s+(\d{4})\b"), "mdy_name"),   # Apr 26, 2021
]

# Context cues (looked for in the ~36 chars before a number) that mark it as a
# reference threshold / normal range rather than an observed value.
_THRESHOLD_CUES = ("desirable", "optimal", "reference", "normal", "range", "recommended",
                   "less than", "greater than", "level <", "level =", "level >", "< ", "> ",
                   "upto", "up to", "target")


def _norm_num(s: str) -> str:
    return s.replace(",", "")


def _norm_date(kind: str, g: tuple) -> str | None:
    """Return YYYY-MM-DD or None."""
    try:
        if kind == "iso":
            y, m, d = int(g[0]), int(g[1]), int(g[2])
        elif kind == "dmy_name":
            d, m, y = int(g[0]), _MONTHS.get(g[1][:3].lower()), int(g[2])
        elif kind == "mdy_name":
            m, d, y = _MONTHS.get(g[0][:3].lower()), int(g[1]), int(g[2])
        elif kind == "dmy_num":
            d, m = int(g[0]), int(g[1])
            y = int(g[2]) + (2000 if int(g[2]) < 100 else 0)
        else:
            return None
        if not m or not (1 <= m <= 12) or not (1 <= d <= 31):
            return None
        return f"{y:04d}-{m:02d}-{d:02d}"
    except (ValueError, TypeError, AttributeError):
        return None


def _envelope_blob(fields: dict) -> tuple[str, set[str]]:
    """A flat string of every envelope value + the set of normalized dates within it."""
    blob = json.dumps(fields, default=str)
    env_dates: set[str] = set()
    for pat, kind in _DATE_PATTERNS:
        for mo in pat.finditer(blob):
            nd = _norm_date(kind, mo.groups())
            if nd:
                env_dates.add(nd)
    return blob, env_dates


def analyze(src: str, fields: dict, *, doc_id: str | None = None,
            doc_type: str | None = None, max_list: int = 30) -> dict:
    """Pure coverage analysis over a document's source text + extraction envelope.
    Separated from DB access so it is unit-testable with plain strings/dicts."""
    src = src or ""
    blob, env_dates = _envelope_blob(fields if isinstance(fields, dict) else {})
    env_nums = {_norm_num(n) for n in _DECIMAL.findall(blob)}

    # dedupe by (kind, normalized, threshold) — a value printed 3x counts once.
    seen: set = set()
    considered = captured = reference = 0
    unstructured: list[dict] = []

    for mo in _DECIMAL.finditer(src):
        raw = mo.group(0)
        norm = _norm_num(raw)
        before = src[max(0, mo.start() - 36):mo.start()].lower()
        is_ref = any(cue in before for cue in _THRESHOLD_CUES)
        key = ("num", norm, is_ref)
        if key in seen:
            continue
        seen.add(key)
        if is_ref:
            reference += 1
            continue
        considered += 1
        if norm in env_nums or raw in blob:
            captured += 1
        elif len(unstructured) < max_list:
            ctx = src[max(0, mo.start() - 24):mo.end() + 12].replace("\n", " ").strip()
            unstructured.append({"value": raw, "kind": "amount", "context": ctx})

    for pat, kind in _DATE_PATTERNS:
        for mo in pat.finditer(src):
            raw = mo.group(0)
            nd = _norm_date(kind, mo.groups())
            key = ("date", nd or raw, False)
            if key in seen:
                continue
            seen.add(key)
            considered += 1
            if (nd and nd in env_dates) or raw in blob:
                captured += 1
            elif len(unstructured) < max_list:
                ctx = src[max(0, mo.start() - 24):mo.end() + 12].replace("\n", " ").strip()
                unstructured.append({"value": raw, "kind": "date", "context": ctx})

    salient = considered + reference
    pct = round(100 * captured / considered) if considered else None
    grade = ("na" if not considered
             else "green" if pct >= 90 else "amber" if pct >= 70 else "red")
    return {
        "docId": doc_id,
        "docType": doc_type,
        "lossless": bool(src),  # the salient tokens are drawn from the chunks → all searchable
        "salientCount": salient,
        "referenceExcluded": reference,
        "structured": {"considered": considered, "captured": captured, "pct": pct},
        "unstructured": unstructured,
        "grade": grade,
        "note": ("All page values are captured verbatim in the indexed chunks (searchable in "
                 "chat). 'Structured' counts how many salient numbers/dates were also mapped "
                 "into typed fields; reference thresholds are excluded from that count."),
    }


def coverage_report(db: Session, doc: Document, *, max_list: int = 30) -> dict:
    """Gather a doc's source text (chunks) + extraction envelope and run analyze()."""
    ef = doc.extracted_fields if isinstance(doc.extracted_fields, dict) else {}
    fields = ef.get("fields") if isinstance(ef.get("fields"), dict) else ef
    src = "\n".join(
        c.text or "" for c in db.scalars(
            select(DocumentChunk).where(DocumentChunk.document_pk == doc.pk)
            .order_by(DocumentChunk.chunk_index)))
    return analyze(src, fields if isinstance(fields, dict) else {},
                   doc_id=doc.id_external, doc_type=doc.doc_type, max_list=max_list)
