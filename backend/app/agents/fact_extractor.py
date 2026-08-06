"""Universal fact extractor — layer 1 of the structured-facts architecture.

The matcher and classifier already label what TYPE a document is. This
extractor runs AFTER the classifier and pulls the document-type-specific
structured fields (parties, dates, totals, signatures, etc.) into JSON.

It is the deterministic answer to "is this signed?", "who are the parties?",
"when does it expire?", "what's the total?" — questions where a regex layer
on the chat router doesn't scale. The chat router consults extracted_fields
first; if the answer is there it grounds against the source chunks. Only
open-ended / unstructured questions fall through to retrieval.

This is the *text-based sibling* of `kyc_extractor.py` (which is vision-only):
- KYC extractor: image inputs only (passports, IDs, ID cards). Skips PDFs.
- Fact extractor: text-extractable PDFs / docs. Uses the chunks that
  ingestion already wrote, so no extra parsing cost.

Schemas are routed from classifier doc_type via DOC_TYPE_TO_SCHEMA. Adding a
new doc-type is a one-stanza change (new entry in SCHEMAS + one route line).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_current_tenant
from app.feature_flags import is_enabled
from app.llm import ledger
from app.llm.prompts import get_prompt
from app.orm import DocumentChunk
from app.repositories import documents as docs_repo
from app.agents.fact_schemas import DOC_TYPE_TO_SCHEMA, FactSchema, SCHEMAS  # noqa: F401


# Fact extraction is the heaviest single LLM call in the platform — Claude
# Haiku tool-use with 4096 max tokens, often outputting hundreds of line
# items. Output cost dominates input cost here; rough blended rate, see
# TODO #11 for proper split.
_FACT_RATE_USD_PER_MTOK = 4.0

# Patterns that mark a chunk as likely belonging to a signature, attestation,
# or dating page. These rarely contain the literal word "signed" — the actual
# attestation reads "I have read, understood and agree to abide..." or
# "Signature Date Name of signatory:". Without explicitly pulling these
# chunks, the extractor misses signatures on long docs because the signature
# page tends to sit at chunk index 5-10 (after a 2-page cover) rather than
# in the first-5 or last-5 window. See doc_chat.py for the parallel patterns.
_SIG_PATTERNS = [
    "%signature%", "%signatory%", "%I have read%", "%agree to abide%",
    "%Name of signatory%", "%duly signed%", "%Date Name of%",
    "%have executed%", "%in witness whereof%",
]

log = logging.getLogger(__name__)


# ── Classifier doc_type → extractor schema key ────────────────────────────
# When the classifier returns one of these doc_types, dispatch to the
# matching schema below. Types not listed here fall through (no facts
# extracted — the chat router falls back to retrieval as it always did).


# ── LLM call ───────────────────────────────────────────────────────────────
# Extraction routes through the LLM gateway so admin overrides (provider + model)
# take effect. The gateway dispatches to the right backend (DashScope, DeepSeek,
# Google Gemini, OpenRouter, …) based on the model prefix.


def _effective_extraction_model() -> str:
    """Resolve the effective extraction model from admin overrides → env → default."""
    try:
        from app.model_registry import resolve_model
        # Load DB admin overrides so operator changes in the AI Operations tab take effect
        overrides = None
        try:
            from app.db import SessionLocal as _SessionLocal
            from app.orm import RoutingConfig
            with _SessionLocal() as _s:
                _rc = _s.get(RoutingConfig, 1)
                if _rc and _rc.config:
                    overrides = (_rc.config or {}).get("operations")
        except Exception:
            pass
        return resolve_model("fact_extraction", overrides)
    except Exception:
        return "dashscope/qwen-max"


# Phase 3 · a request-scoped override lets "re-analyze with the best model" (and, later, the
# auto-escalation router) run extraction on a stronger model without threading a param
# through every internal call. Set it around extract(); it falls back to _effective_extraction_model.
import contextvars as _ctxvars  # noqa: E402
_model_override: "_ctxvars.ContextVar[str | None]" = _ctxvars.ContextVar("extract_model_override", default=None)


def _active_model() -> str:
    return _model_override.get() or _effective_extraction_model()


def _salvage_truncated_arrays(args: dict, args_str: str) -> dict:
    """T1.3 · When json-repair recovers a malformed payload, it sometimes
    silently drops array tail items (one unescaped quote on item 48 of 100
    → only 47 items returned). Detect by counting object opens `{` inside
    the raw array source vs the parsed array length. If divergence is
    >20%, try a per-item brace-balance scan to salvage individual objects.

    Best-effort · returns args unchanged if salvage isn't possible.
    """
    # json-repair can recover a payload into a non-dict (e.g. a top-level list or
    # a bare string) when the model emits badly-malformed JSON. The salvage logic
    # below is dict-shaped, so guard: a non-dict is returned untouched (the caller
    # then validates shape) rather than crashing on dict(args).
    if not isinstance(args, dict):
        return args
    try:
        return _salvage_impl(args, args_str)
    except Exception as e:  # noqa: BLE001 — best-effort: a salvage hiccup must NOT
        # abort extraction. Return the (already json-repaired) args unchanged so a
        # recovered dict is still used. (Regression: a malformed `identifiers`
        # array made salvage raise and dropped a whole doc's extraction.)
        log.warning("fact_extractor: array-salvage skipped (non-fatal): %s", e)
        return args


def _salvage_impl(args: dict, args_str: str) -> dict:
    out = dict(args)
    for key, val in list(args.items()):
        if not isinstance(val, list) or not val:
            continue
        if not all(isinstance(item, dict) for item in val):
            continue  # only salvage arrays-of-objects (transactions, line items)
        parsed_n = len(val)
        # Locate the source array for this key (rough — finds first occurrence).
        key_pos = args_str.find(f'"{key}"')
        if key_pos < 0:
            continue
        # Find the `[` after the key.
        bracket_pos = args_str.find("[", key_pos)
        if bracket_pos < 0:
            continue
        # Count `{` from bracket to first matching `]` (or end of string).
        depth = 0
        end = bracket_pos
        for i in range(bracket_pos, len(args_str)):
            c = args_str[i]
            if c == "[":
                depth += 1
            elif c == "]":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        else:
            end = len(args_str)
        slice_ = args_str[bracket_pos:end]
        source_obj_count = slice_.count("{")
        # If parsed count is close enough (within 20%), assume no truncation.
        if parsed_n >= int(source_obj_count * 0.8):
            continue
        # Salvage attempt · brace-balance scan.
        salvaged: list[dict] = []
        depth = 0
        start_idx = None
        for i, c in enumerate(slice_):
            if c == "{":
                if depth == 0:
                    start_idx = i
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0 and start_idx is not None:
                    obj_str = slice_[start_idx:i + 1]
                    start_idx = None
                    # Try strict parse, then json-repair on a single object.
                    try:
                        obj = json.loads(obj_str)
                        salvaged.append(obj)
                    except Exception:
                        try:
                            from json_repair import repair_json
                            repaired = repair_json(obj_str)
                            obj = json.loads(repaired) if isinstance(repaired, str) else repaired
                            if isinstance(obj, dict):
                                salvaged.append(obj)
                        except Exception:
                            pass
        if len(salvaged) > parsed_n:
            log.warning(
                "fact_extractor: salvaged %d items for key=%r (was %d, source had ~%d)",
                len(salvaged), key, parsed_n, source_obj_count,
            )
            out[key] = salvaged
        else:
            log.warning(
                "fact_extractor: truncation suspected for key=%r — parsed=%d source=%d, salvage didn't improve (kept parsed)",
                key, parsed_n, source_obj_count,
            )
    return out
_MAX_TEXT_CHARS = 12000  # ~3K tokens — large enough for parties + signature pages
# M46 · documents universal mode sends the WHOLE doc (all chunks) so multi-page
# statements keep every transaction row — the intro+tail window drops the middle.
_MAX_TEXT_CHARS_FULL = 60000  # ~15K tokens; models here have 200K context


@dataclass
class ExtractionResult:
    schema_key: str
    fields: dict[str, Any]
    confidence: float
    notes: str
    model: str
    chunk_refs: list[dict[str, Any]]   # which chunks we sent — for chat-side citation
    field_bboxes: dict[str, dict[str, Any]]  # tightened per-field bboxes
    raw_response: dict
    # G4 · per-field confidence (0..1); drives the G7 review queue. Defaulted so
    # any other construction site keeps working.
    field_confidence: dict[str, float] = field(default_factory=dict)

    def to_jsonb(self) -> dict[str, Any]:
        """Shape for documents.extracted_fields. Matches the KYC shape so
        downstream code (chat router, UI) can treat both interchangeably.
        `field_bboxes` is a sidecar map: field_name → {page, x0, y0, x1, y1,
        chunk_pk}. Frontend uses it for tight citation rectangles (click a
        Key Facts row to land on the precise span in the PDF viewer)."""
        return {
            "doc_type": self.schema_key,
            "fields": self.fields,
            "confidence": self.confidence,
            "notes": self.notes,
            "model": self.model,
            "chunk_refs": self.chunk_refs,
            "field_bboxes": self.field_bboxes,
            # G4 · per-field confidence (field_name → 0..1).
            "field_confidence": self.field_confidence,
            "extracted_at": datetime.now(timezone.utc).isoformat(),
            # M44.P9.9 · per-schema validators emit warnings the UI
            # renders as ⚠ chips on the Key Facts card.
            "warnings": _validate_fields(self.schema_key, self.fields),
        }


# ── M44.P9.9 · Anomaly detection ────────────────────────────────────────
# Each schema runs its own validators after extraction. Warnings are
# advisory — they don't block extraction, they surface to the reviewer
# so they can spot extraction mistakes or genuinely odd documents.

def _validate_fields(schema_key: str, fields: dict) -> list[dict]:
    """Run all validators applicable to this schema. Returns a list of
    {severity, code, message, field_path?} dicts. Empty when no issues.

    Severity: 'info' | 'warning' | 'error'.
    """
    out: list[dict] = []
    try:
        # Common validators across all schemas
        out.extend(_check_date_consistency(fields))
        out.extend(_check_signed_when_required(fields, schema_key))

        # Schema-specific
        if schema_key == "invoice":
            out.extend(_check_invoice_total_vs_lines(fields))
            out.extend(_check_invoice_due_vs_issue(fields))
        elif schema_key == "bank_statement":
            out.extend(_check_balance_consistency(fields))
        elif schema_key == "insurance_certificate":
            out.extend(_check_insurance_already_expired(fields))
        elif schema_key == "universal":
            out.extend(_check_universal_arrays_present(fields))
    except Exception:  # noqa: BLE001
        # NEVER raise from validators · they're advisory only.
        pass
    return out


def _money_to_float(s) -> float | None:
    """Parse 'USD 1,234.56' / '$1234' / '1234.56' to a float. Returns None
    when unparseable."""
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    if not isinstance(s, str):
        return None
    import re as _re
    cleaned = _re.sub(r"[^0-9.\-]", "", s.replace(",", ""))
    try:
        return float(cleaned) if cleaned else None
    except ValueError:
        return None


def _parse_iso(s):
    """Loose ISO date parse."""
    if not isinstance(s, str) or not s.strip():
        return None
    import datetime as _dt
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y",
                "%d %b %Y", "%d %B %Y", "%b %d, %Y", "%d-%b-%y"):
        try:
            return _dt.datetime.strptime(s.strip(), fmt).date()
        except ValueError:
            continue
    return None


def _check_date_consistency(fields: dict) -> list[dict]:
    """If effective_date > expiry_date, flag it."""
    eff = _parse_iso(fields.get("effective_date") or fields.get("issue_date"))
    exp = _parse_iso(fields.get("expiry_date") or fields.get("valid_until"))
    if eff and exp and eff > exp:
        return [{
            "severity": "warning",
            "code": "date_inverted",
            "message": f"Effective date {eff} is AFTER expiry date {exp} · likely extraction error.",
            "field_path": "fields.effective_date",
        }]
    return []


def _check_signed_when_required(fields: dict, schema_key: str) -> list[dict]:
    """Agreements / certificates lacking signatures get a soft flag."""
    if schema_key not in ("agreement", "insurance_certificate", "certificate"):
        return []
    sigs = fields.get("signature_blocks") or fields.get("signatures")
    if isinstance(sigs, list) and any(
        isinstance(s, dict) and (s.get("name") or s.get("signatory"))
        for s in sigs
    ):
        return []
    return [{
        "severity": "info",
        "code": "no_signature",
        "message": "No signature block extracted · may be unsigned, or signature page wasn't reached.",
    }]


def _check_invoice_total_vs_lines(fields: dict) -> list[dict]:
    """Sum of line_items[].amount should equal total. Allow 1% tolerance
    for rounding (often the LLM extracts subtotal as 'total')."""
    total = _money_to_float(fields.get("total") or fields.get("grand_total") or fields.get("invoice_total"))
    items = fields.get("line_items") or []
    if total is None or not isinstance(items, list) or not items:
        return []
    line_sum = 0.0
    for it in items:
        if isinstance(it, dict):
            v = _money_to_float(it.get("amount") or it.get("total"))
            if v is not None:
                line_sum += v
    if line_sum <= 0:
        return []
    tolerance = max(total * 0.01, 1.0)  # 1% or $1, whichever is larger
    if abs(total - line_sum) > tolerance:
        return [{
            "severity": "warning",
            "code": "total_mismatch",
            "message": (
                f"Invoice total ({total:.2f}) doesn't match sum of line items "
                f"({line_sum:.2f}). Difference: {abs(total - line_sum):.2f}."
            ),
            "field_path": "fields.total",
        }]
    return []


def _check_invoice_due_vs_issue(fields: dict) -> list[dict]:
    """Due date before issue date is suspicious."""
    issue = _parse_iso(fields.get("issue_date"))
    due = _parse_iso(fields.get("due_date"))
    if issue and due and due < issue:
        return [{
            "severity": "warning",
            "code": "due_before_issue",
            "message": f"Due date {due} is BEFORE issue date {issue} · likely extraction error.",
        }]
    return []


def _check_balance_consistency(fields: dict) -> list[dict]:
    """opening_balance + sum(transactions) ≈ closing_balance."""
    opening = _money_to_float(fields.get("opening_balance"))
    closing = _money_to_float(fields.get("closing_balance"))
    txns = fields.get("transactions") or fields.get("top_transactions") or []
    if opening is None or closing is None or not isinstance(txns, list):
        return []
    net = 0.0
    for t in txns:
        if not isinstance(t, dict):
            continue
        amt = _money_to_float(t.get("amount"))
        direction = (t.get("direction") or "").lower()
        if amt is None:
            continue
        if direction == "debit":
            net -= amt
        elif direction == "credit":
            net += amt
        else:
            net += amt  # assume positive when ambiguous
    expected = opening + net
    tolerance = max(abs(closing) * 0.01, 1.0)
    if abs(expected - closing) > tolerance:
        return [{
            "severity": "info",
            "code": "balance_imprecise",
            "message": (
                f"Opening + transactions ({expected:.2f}) doesn't quite match "
                f"closing balance ({closing:.2f}). May be unmodeled fees."
            ),
        }]
    return []


def _check_insurance_already_expired(fields: dict) -> list[dict]:
    """Flag expired insurance policies prominently."""
    import datetime as _dt
    exp = _parse_iso(fields.get("expiry_date") or fields.get("valid_until"))
    if exp and exp < _dt.date.today():
        days = (_dt.date.today() - exp).days
        return [{
            "severity": "error",
            "code": "policy_expired",
            "message": f"Policy expired on {exp} ({days} days ago) · NOT in force.",
            "field_path": "fields.expiry_date",
        }]
    return []


def _check_universal_arrays_present(fields: dict) -> list[dict]:
    """Universal-extracted doc with all arrays empty is a sign of low-
    quality extraction (LLM didn't find anything to slot)."""
    arrays = ("identifiers", "amounts", "dates", "key_facts", "parties")
    populated = sum(
        1 for a in arrays
        if isinstance(fields.get(a), list) and fields.get(a)
    )
    if populated == 0 and not (fields.get("title") or fields.get("issuer")):
        return [{
            "severity": "info",
            "code": "low_extraction",
            "message": "Universal extractor found no structured slots · doc may need manual review.",
        }]
    return []


# LLM normalizes dates to YYYY-MM-DD; PDFs almost never use that form.
# Generate plausible alternate forms so bbox search can still hit.
_DATE_RX = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_MONTH_NAMES = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]


def _text_layer_fallback(doc, unlocated: dict, bboxes: dict) -> None:
    """M47 · Match field values against word-level text_layer bboxes.
    When page.search_for() fails (LLM normalised the value differently),
    find the value in the word blocks extracted by _extract_text_layer().
    Normalizes both sides: lowercase, strip, collapse whitespace. Zero cost."""
    ef = doc.extracted_fields or {}
    text_layer = ef.get("text_layer") or []
    if not text_layer:
        return

    # Index words by page for fast lookup
    by_page: dict[int, list[dict]] = {}
    for w in text_layer:
        by_page.setdefault(w.get("page", 0), []).append(w)

    for fname, fval in unlocated.items():
        fval_str = str(fval).strip()
        if len(fval_str) < 3:
            continue
        fval_norm = fval_str.lower()

        # Build search needles: full string, no-space, date variants
        needles = [fval_norm]
        no_space = "".join(fval_norm.split())
        if no_space != fval_norm:
            needles.append(no_space)
        needles.extend(_date_search_variants(fval_str))

        for pg, words in by_page.items():
            page_spaced = " ".join(w.get("text", "").lower().strip() for w in words)
            page_nospace = "".join(w.get("text", "").lower().strip() for w in words)

            # Try full-string match first
            matched_words = None
            for needle in needles:
                needle_lower = needle.lower()
                idx = page_spaced.find(needle_lower)
                if idx < 0: idx = page_nospace.find(needle_lower)
                if idx < 0 and len(needle) > 20: idx = page_spaced.find(needle_lower[:20])
                if idx >= 0:
                    char_pos = 0
                    matched_words = []
                    for w in words:
                        wt = w.get("text", "").lower().strip()
                        if not wt: continue
                        w_start = char_pos; w_end = char_pos + len(wt)
                        char_pos = w_end + 1
                        if w_end >= idx and w_start <= idx + len(needle):
                            matched_words.append(w)
                    if matched_words: break

            # Token proximity fallback: find key tokens, verify they're near each other
            if not matched_words:
                key_tokens = [t for t in fval_norm.replace(",","").split() if len(t) > 3]
                if len(key_tokens) >= 2:
                    token_matches = []
                    for token in key_tokens:
                        for w in words:
                            wt = w.get("text", "").lower().strip()
                            if token in wt or wt in token:
                                token_matches.append(w)
                                break
                    # Check if matched tokens are on same page and within 200px vertically
                    if len(token_matches) >= max(2, len(key_tokens) * 0.5):
                        y_vals = [w["y0"] for w in token_matches]
                        if max(y_vals) - min(y_vals) < 200:
                            matched_words = token_matches

            if matched_words:
                bboxes[fname] = {
                    "page": pg,
                    "x0": round(min(w["x0"] for w in matched_words), 1),
                    "y0": round(min(w["y0"] for w in matched_words), 1),
                    "x1": round(max(w["x1"] for w in matched_words), 1),
                    "y1": round(max(w["y1"] for w in matched_words), 1),
                    "page_w": matched_words[0].get("page_w", 0),
                    "page_h": matched_words[0].get("page_h", 0),
                    "chunk_pk": 0,
                }
                break


def _pdfplumber_fallback(db, document_pk: int, unlocated: dict, bboxes: dict) -> None:
    """M47 · pdfplumber char-level bbox extraction. Extracts every character
    with position, then matches field values by normalized substring. Catches
    multi-line values and split formatting that search_for + text_layer miss."""
    import io as _io
    from app import storage
    from sqlalchemy import text as _text

    try:
        row = db.execute(_text("SELECT s3_key, mime_type FROM documents WHERE pk=:pk"),
                         {"pk": document_pk}).first()
        if not row or not row[0]:
            return
        buf = _io.BytesIO()
        for chunk in storage.stream_object(row[0]):
            buf.write(chunk)
        pdf_bytes = buf.getvalue()
    except Exception:
        return

    try:
        import pdfplumber
        with pdfplumber.open(_io.BytesIO(pdf_bytes)) as pdf:
            for pg_num, page in enumerate(pdf.pages, start=1):
                chars = page.chars
                if not chars:
                    continue
                pw, ph = page.width, page.height
                # Build page text and char-indexed positions
                page_text = "".join(c.get("text", "") for c in chars)
                page_text_norm = page_text.lower()

                for fname, fval in list(unlocated.items()):
                    if fname in bboxes:
                        continue
                    fval_norm = str(fval).lower().strip()
                    if len(fval_norm) < 3:
                        continue
                    idx = page_text_norm.find(fval_norm)
                    if idx < 0 and len(fval_norm) > 20:
                        idx = page_text_norm.find(fval_norm[:20])
                    if idx < 0:
                        continue

                    # Find chars in the matched range using char positions
                    char_pos = 0
                    matched_chars = []
                    for c in chars:
                        ct = c.get("text", "")
                        cl = len(ct)
                        if char_pos + cl > idx and char_pos < idx + len(fval_norm):
                            matched_chars.append(c)
                        char_pos += cl

                    if matched_chars:
                        bboxes[fname] = {
                            "page": pg_num,
                            "x0": round(min(c["x0"] for c in matched_chars), 1),
                            "y0": round(min(c["top"] for c in matched_chars), 1),
                            "x1": round(max(c["x1"] for c in matched_chars), 1),
                            "y1": round(max(c["bottom"] for c in matched_chars), 1),
                            "page_w": round(pw, 1),
                            "page_h": round(ph, 1),
                            "chunk_pk": 0,
                        }
    except Exception:
        pass


def _date_search_variants(value: str) -> list[str]:
    """For an ISO date, return common rendered variants the PDF might use:
    DD-Mmm-YYYY, DD Mmm YYYY, DD Month YYYY, Mmm DD YYYY, M/D/YYYY, etc.
    Returns empty list if the value isn't a recognisable ISO date."""
    m = _DATE_RX.match(value.strip())
    if not m:
        return []
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    try:
        date(y, mo, d)  # validate
    except ValueError:
        return []
    mon_full = _MONTH_NAMES[mo - 1]
    mon_short = mon_full[:3]
    forms: list[str] = [
        f"{d:02d}-{mon_short}-{y}",       # 08-Jun-2026
        f"{d:02d} {mon_short} {y}",       # 08 Jun 2026
        f"{d:02d} {mon_full} {y}",        # 08 June 2026
        f"{d} {mon_short} {y}",           # 8 Jun 2026
        f"{d} {mon_full} {y}",            # 8 June 2026
        f"{mon_short} {d:02d}, {y}",      # Jun 08, 2026
        f"{mon_short} {d}, {y}",          # Jun 8, 2026
        f"{mon_full} {d}, {y}",           # June 8, 2026
        f"{mo:02d}/{d:02d}/{y}",          # 06/08/2026
        f"{d:02d}/{mo:02d}/{y}",          # 08/06/2026
        f"[{d:02d}-{mon_short}-{y}]",     # [08-Jun-2026]  (bracketed, very common in templates)
    ]
    # Dedup while preserving order
    seen = set()
    out = []
    for f in forms:
        if f not in seen:
            out.append(f)
            seen.add(f)
    return out


def _locate_image_field_bboxes(doc, fields: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Locate each scalar field value on an IMAGE document via Tesseract word boxes.
    Mirrors the PDF path but for raster images (no text layer). Best-effort — on any
    failure (no Tesseract, unreadable image) the frontend falls back to chunk bboxes."""
    bboxes: dict[str, dict[str, Any]] = {}
    if not getattr(doc, "s3_key", None):
        return bboxes
    # Only scalar strings long enough to locate — same rule as the PDF path.
    scalar = {k: v for k, v in (fields or {}).items()
              if isinstance(v, str) and len(v.strip()) >= 3}
    if not scalar:
        return bboxes
    try:
        from app.agents import ocr as ocr_mod
        from app import storage as app_storage
        img = b"".join(app_storage.stream_object(doc.s3_key))
        words, w, h = ocr_mod.extract_words(img)
        if not words or not w or not h:
            return bboxes
        return ocr_mod.locate_fields(words, scalar, w, h) or {}
    except Exception:  # noqa: BLE001 — best-effort; graceful fallback
        return bboxes


def _locate_field_bboxes(
    db: Session,
    document_pk: int,
    fields: dict[str, Any],
    chunk_refs: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """For each scalar string field, find a tight bounding rect on its page
    via PyMuPDF page.search_for(value). Tries the value verbatim first, then
    a 20-char prefix as a fallback for long fields (e.g. addresses, MRZ).

    Returns a map: field_name → {page, x0, y0, x1, y1, chunk_pk}.

    Skips:
      - Non-scalar fields (arrays / objects)
      - Empty values
      - Documents whose source isn't a PDF (vision-OCR'd images: no PDF
        coordinate system, so the frontend uses a synthetic full-page bbox
        from the chunk).
      - Values shorter than 3 chars (too noisy to search precisely).

    Best-effort: failures here just mean the frontend falls back to chunk
    bboxes for those fields — nothing breaks.
    """
    bboxes: dict[str, dict[str, Any]] = {}

    doc = docs_repo.get_row_by_pk(db, document_pk)
    if doc is None:
        return bboxes
    mime = (doc.mime_type or "").lower()
    # Images have no PDF text layer to search — locate each field value on the
    # image itself via Tesseract word boxes (same {x0,y0,x1,y1,page_w,page_h,page}
    # shape the PDF path returns). Reuses the KYC image-locator (ocr.locate_fields).
    if mime.startswith("image/"):
        return _locate_image_field_bboxes(doc, fields)
    # PDFs: search the text layer for each value (below). Non-PDF non-image → none.
    if not chunk_refs or not mime.startswith("application/pdf"):
        return bboxes

    try:
        import fitz  # PyMuPDF
        from app import storage as app_storage

        if not doc.s3_key:
            return bboxes
        buf = b"".join(app_storage.stream_object(doc.s3_key))
        with fitz.open(stream=buf, filetype="pdf") as pdf:
            # Each ref carries a chunk's page; we use the FIRST chunk_ref's
            # page as the default search page. For multi-page docs the right
            # page is usually the same as the intro/signature region we
            # already pulled (parties on page 1, signatures on page 3, etc).
            # We try every chunk_ref's page in order until search_for hits.
            pages_to_try: list[tuple[int, int]] = []  # (1-based page, chunk_pk)
            seen = set()
            for r in chunk_refs:
                pg = r.get("page")
                cpk = r.get("chunk_pk")
                if not pg or pg in seen:
                    continue
                pages_to_try.append((pg, cpk))
                seen.add(pg)

            for fname, fval in fields.items():
                if not isinstance(fval, str):
                    continue
                v = fval.strip()
                if len(v) < 3:
                    continue
                # PyMuPDF.search_for matches verbatim. The LLM normalises
                # values (dates → YYYY-MM-DD, names ALL-CAPS, etc) so verbatim
                # rarely hits. Strategy:
                #   1. Try the value verbatim (catches names, agreement_type,
                #      jurisdiction, doc numbers, etc).
                #   2. For ISO dates, try common rendered forms (DD-Mmm-YYYY,
                #      "DD Month YYYY", bracketed, slashed).
                #   3. Long values get a prefix fallback so addresses match.
                needles = [v[:80]] if len(v) >= 12 else [v]
                needles.extend(_date_search_variants(v))
                if len(v) >= 20:
                    needles.append(v[:20])
                hit = None
                hit_page = None
                hit_cpk = None
                hit_w = hit_h = None
                for pg, cpk in pages_to_try:
                    try:
                        page = pdf.load_page(pg - 1)
                    except Exception:  # noqa: BLE001
                        continue
                    for needle in needles:
                        try:
                            rects = page.search_for(needle, quads=False)
                        except Exception:  # noqa: BLE001
                            rects = []
                        if rects:
                            hit = rects[0]
                            hit_page = pg
                            hit_cpk = cpk
                            # Page native dimensions (points) — the frontend scales
                            # the stored coords by (rendered_px / page_w|h) to draw
                            # the highlight box. Without these the overlay can't
                            # project the bbox and renders nothing.
                            hit_w = float(page.rect.width)
                            hit_h = float(page.rect.height)
                            break
                    if hit:
                        break
                if hit and hit_page:
                    bboxes[fname] = {
                        "page": hit_page,
                        "x0": float(hit.x0),
                        "y0": float(hit.y0),
                        "x1": float(hit.x1),
                        "y1": float(hit.y1),
                        "page_w": hit_w,
                        "page_h": hit_h,
                        "chunk_pk": hit_cpk,
                    }

            # M47 · Text-layer fallback: for fields search_for missed, match the
            # field value against word-level bboxes from _extract_text_layer().
            # Normalizes both sides (lowercase, strip) — catches "K5254016D" vs
            # "K5254016D " mismatches. Zero LLM cost.
            unlocated = {k: v for k, v in fields.items()
                         if isinstance(v, str) and len(v.strip()) >= 3 and k not in bboxes}
            if unlocated:
                _text_layer_fallback(doc, unlocated, bboxes)

            # M47 · pdfplumber fallback: char-level text extraction with bbox.
            # Catches multi-line values, split formatting, and values that search_for
            # and text_layer both missed. pdfplumber extracts every character with
            # position → we match field values by normalized substring search.
            unlocated = {k: v for k, v in fields.items()
                         if isinstance(v, str) and len(v.strip()) >= 3 and k not in bboxes}
            if unlocated:
                _pdfplumber_fallback(db, document_pk, unlocated, bboxes)

            # Scanned-PDF fallback: for scalar fields the text layer couldn't locate,
            # if a page has NO extractable text (it's a scanned image inside a PDF),
            # render it to a raster and reuse the Tesseract image locator.
            unlocated = {k: v for k, v in fields.items()
                         if isinstance(v, str) and len(v.strip()) >= 3 and k not in bboxes}
            if unlocated:
                from app.agents import ocr as ocr_mod
                for pg, cpk in pages_to_try:
                    if not unlocated:
                        break
                    try:
                        page = pdf.load_page(pg - 1)
                        if page.get_text().strip():
                            continue  # has a text layer — search_for already handled it
                        png = page.get_pixmap(dpi=150).tobytes("png")
                        words, iw, ih = ocr_mod.extract_words(png)
                        if not words:
                            continue
                        for fname, box in (ocr_mod.locate_fields(words, unlocated, iw, ih) or {}).items():
                            entry = dict(box)
                            entry["page"] = pg
                            entry["chunk_pk"] = cpk
                            bboxes[fname] = entry
                            unlocated.pop(fname, None)
                    except Exception:  # noqa: BLE001 — best-effort
                        continue

            # Array-of-objects fields: signature_blocks[], line_items[],
            # top_transactions[], parties[]. Each entry typically has a
            # `page` attribute (the LLM filled it from our schema) and an
            # identifying scalar like `signatory_name`. We emit one bbox
            # entry per array item, keyed `{fieldname}[idx]`, using the
            # item's declared page as the search target. Even when no
            # tight bbox can be located, we still emit a page-only entry
            # so the citation chip lands on the right page.
            _IDENTIFY_KEYS = ("signatory_name", "name", "description", "holder_name", "policy_title")
            for fname, fval in fields.items():
                if not isinstance(fval, list):
                    continue
                for idx, item in enumerate(fval):
                    if not isinstance(item, dict):
                        continue
                    item_page = item.get("page")
                    if not isinstance(item_page, int):
                        continue
                    # Try to find a tight bbox on the item's declared page
                    identifier = None
                    for ik in _IDENTIFY_KEYS:
                        v = item.get(ik)
                        if isinstance(v, str) and len(v.strip()) >= 3:
                            identifier = v.strip()
                            break
                    rect = None
                    try:
                        page = pdf.load_page(item_page - 1)
                    except Exception:  # noqa: BLE001
                        page = None
                    if page and identifier:
                        try:
                            rects = page.search_for(identifier[:80], quads=False)
                        except Exception:  # noqa: BLE001
                            rects = []
                        if rects:
                            rect = rects[0]

                    entry: dict[str, Any] = {"page": item_page}
                    if rect:
                        entry.update({
                            "x0": float(rect.x0), "y0": float(rect.y0),
                            "x1": float(rect.x1), "y1": float(rect.y1),
                        })
                        if page is not None:
                            entry["page_w"] = float(page.rect.width)
                            entry["page_h"] = float(page.rect.height)
                    bboxes[f"{fname}[{idx}]"] = entry

    except Exception as e:  # noqa: BLE001
        log.warning("field-bbox tightening failed for doc pk=%s: %s — leaving bboxes empty", document_pk, e)
        return {}
    return bboxes


def _build_text_excerpt(db: Session, document_pk: int, *, full: bool = False) -> tuple[str, list[dict[str, Any]]]:
    """Build the prompt excerpt + the citation map.

    `full=True` (documents universal mode) sends the WHOLE document — every
    chunk, larger char cap — so multi-page statements/reports keep all their
    rows. Otherwise the intro+signature+tail window applies.

    Selection strategy (in priority order):
      1. Intro chunks (first 5) — parties, agreement title, effective date
         live on page 1 of every agreement / invoice / certificate.
      2. Attestation chunks — pulled via ILIKE on signature patterns so the
         signature page is always included even when it sits in the middle
         of a long doc (e.g. a 60-chunk SLA's signature page is at chunk 5,
         outside the first-5 + last-5 window).
      3. Tail chunks (last 5) — execution / witness clauses, total amounts,
         appendix tables typically live here.

    Deduped in selection order; preserves chunk_index ordering within the
    excerpt so the LLM sees the document roughly front-to-back.

    Returns (text, chunk_refs). chunk_refs is the list of
    {chunk_pk, chunk_index, page} so the chat router can cite which chunk a
    fact came from.
    """
    tid = get_current_tenant()
    all_chunks = docs_repo.chunks_for_doc(db, document_pk, tenant_id=tid)
    if not all_chunks:
        return "", []

    cap = _MAX_TEXT_CHARS_FULL if full else _MAX_TEXT_CHARS
    if full:
        # Read everything that fits. For docs LARGER than the budget, sample chunks EVENLY
        # front-to-back (plus the last chunk) instead of front-loading until the cap — otherwise
        # a very long statement/report loses its tail pages entirely.
        total = sum(len(" ".join((c.text or "").split())) + 24 for c in all_chunks)
        if total <= cap:
            selected_pks = {c.pk for c in all_chunks}
        else:
            stride = max(1, round(total / cap))
            selected_pks = {c.pk for c in all_chunks[::stride]} | {all_chunks[-1].pk}
    elif len(all_chunks) <= 12:
        selected_pks = {c.pk for c in all_chunks}
    else:
        sig_chunks = db.scalars(
            select(DocumentChunk)
            .where(
                DocumentChunk.tenant_id == tid,
                DocumentChunk.document_pk == document_pk,
                or_(*(DocumentChunk.text.ilike(p) for p in _SIG_PATTERNS)),
            )
            .order_by(DocumentChunk.chunk_index)
            .limit(4)
        ).all()
        selected_pks = {c.pk for c in all_chunks[:5]} \
            | {c.pk for c in sig_chunks} \
            | {c.pk for c in all_chunks[-5:]}

    # Iterate in chunk_index order so the LLM sees the doc front-to-back.
    parts: list[str] = []
    refs: list[dict[str, Any]] = []
    running = 0
    for c in all_chunks:
        if c.pk not in selected_pks:
            continue
        body = " ".join((c.text or "").split())
        if not body:
            continue
        block = f"[chunk {c.chunk_index} · page {c.page}]\n{body}"
        if running + len(block) > cap:
            remaining = cap - running
            if remaining > 200:
                parts.append(block[:remaining] + "\n[...truncated]")
                refs.append({"chunk_pk": c.pk, "chunk_index": c.chunk_index, "page": c.page,
                             "bbox": c.bbox if c.bbox else None})
            break
        parts.append(block)
        refs.append({"chunk_pk": c.pk, "chunk_index": c.chunk_index, "page": c.page,
                     "bbox": c.bbox if c.bbox else None})
        running += len(block) + 2

    return "\n\n".join(parts), refs


def _build_prompt(schema: FactSchema) -> str:
    return (
        f"You are extracting structured facts from a document.\n\n"
        f"Expected document type: {schema.label}\n\n"
        f"Instructions:\n"
        f"- Read the document excerpts carefully.\n"
        f"- Fill in every field you can read with high confidence.\n"
        f"- Use empty string \"\" (not 'unknown') for unreadable string fields.\n"
        f"- Use empty array [] for unreadable list fields.\n"
        f"- For dates always use YYYY-MM-DD format.\n"
        f"- For monetary values keep the currency / symbol as printed.\n"
        f"- Nationality is NOT race/ethnicity and NOT place of birth. Never copy a 'Race' "
        f"or 'Country/Place of Birth' value into a nationality/citizenship field. Some "
        f"national ID cards (e.g. Singapore NRIC, Malaysian MyKad) print Race and Country "
        f"of Birth but no nationality — if nationality is not explicitly printed, infer it "
        f"from the issuing country/authority (a national ID is issued by the country of "
        f"citizenship, e.g. Republic of Singapore → 'Singaporean'); otherwise leave it blank.\n"
        f"- Quotes in evidence_quote fields should be ≤ 120 chars verbatim from the doc.\n"
        f"- Set _doc_confidence reflecting your confidence the doc matches the expected type AND the fields are correct.\n"
        f"- If the document is clearly a different type, set _doc_confidence < 0.4 and leave fields blank.\n\n"
        f"Call the record_doc_facts tool with your extraction. Do NOT also write a text response."
    )


_VERIFY_KEYS = ("records", "parties", "dates", "amounts", "identifiers", "key_facts")
# Verify runs against the WHOLE doc (not just the first 12K) so it can find rows on later pages.
_VERIFY_TEXT_CHARS = 55000


def _array_of_objects_keys(fields: dict) -> list[str]:
    """Repeating-ROW fields (transactions, line_items, holdings, …) = arrays of dicts."""
    return [k for k, v in (fields or {}).items()
            if isinstance(v, list) and v and isinstance(v[0], dict)]


def _row_sig(row) -> str:
    """Dedup key for a table row — date + amount + normalized description. Order-insensitive on keys."""
    if not isinstance(row, dict):
        return json.dumps(row, sort_keys=True, ensure_ascii=False)[:120]
    low = {str(k).lower(): v for k, v in row.items()}
    def g(*names):
        for n in names:
            if low.get(n) not in (None, ""):
                return str(low[n]).strip().lower()
        return ""
    desc = re.sub(r"[^a-z0-9]+", "", g("description", "merchant", "name", "label", "narrative", "detail"))[:26]
    amt = re.sub(r"[^0-9.]", "", g("amount", "value", "debit", "credit", "total"))
    return f"{g('date', 'txn_date', 'posting_date', 'transaction_date')}|{amt}|{desc}"


def _self_verify(db: Session, document_pk: int, text: str, fields: dict, settings) -> dict:
    """Completeness reconciliation (xpenseaiq-style). For the repeating-ROW array fields the first
    pass produced (transactions / line_items / holdings / records / …), ask the model to list ONLY
    the rows that appear in the DOCUMENT but are MISSING from what we extracted; merge deduped; loop
    until a round adds nothing (max 3). This is what recovers the middle-page statement rows the
    single-shot extraction under-lists. Best-effort — returns `fields` unchanged on failure."""
    import time as _time
    merged = dict(fields)
    row_keys = _array_of_objects_keys(merged) or []
    # If no repeating-row fields at all, fall back to reconciling the universal arrays only.
    target_keys = row_keys or [k for k in _VERIFY_KEYS if isinstance(merged.get(k), list)]
    if not target_keys:
        return fields
    doc_text = (text or "")[:_VERIFY_TEXT_CHARS]
    sys_prompt = get_prompt("extraction_verify")
    total_added = 0
    for _round in range(3):
        snapshot = {k: merged.get(k) for k in target_keys if merged.get(k)}
        captured = json.dumps(snapshot, ensure_ascii=False)[:9000]
        from app.llm.gateway import call as gateway_call, Message as GatewayMessage

        model = _active_model()
        msgs = [
            GatewayMessage(role="system", content=sys_prompt),
            GatewayMessage(role="user", content=f"DOCUMENT:\n{doc_text}\n\nALREADY EXTRACTED (do not repeat these):\n{captured}\n\nReturn the JSON of MISSING rows now."),
        ]
        t0 = _time.perf_counter()
        try:
            result = gateway_call(
                model=model, messages=msgs, temperature=0, max_tokens=8192,
                structured=True,
                tenant_id=settings.tenant_id,
                task_kind="extract_verify",
            )
        except Exception:  # noqa: BLE001
            break
        ledger.record_call(
            db, task="extract_verify", tier="t2", provider=result.provider, model=result.model,
            document_pk=document_pk,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            latency_ms=result.latency_ms, status="ok",
        )
        content = (result.text or "{}").strip()
        try:
            additions = json.loads(content)
        except json.JSONDecodeError:
            from json_repair import repair_json
            repaired = repair_json(content)
            additions = json.loads(repaired) if isinstance(repaired, str) else repaired
        if not isinstance(additions, dict):
            break
        round_added = 0
        for key, new_items in additions.items():
            if not isinstance(new_items, list) or not new_items:
                continue
            base = merged.get(key) if isinstance(merged.get(key), list) else []
            seen = {_row_sig(r) for r in base}
            fresh = [r for r in new_items if _row_sig(r) not in seen]
            if fresh:
                merged[key] = base + fresh
                round_added += len(fresh)
        total_added += round_added
        if round_added == 0:
            break
    if total_added:
        log.info("fact_extractor: self-verify reconciled %d missing row(s) into doc pk=%s across keys %s",
                 total_added, document_pk, target_keys)
    return merged


# Move-1 PR1 · the universal base scalars are present on EVERY universal doc, so
# they carry no doc-type-distinguishing signal. The learned-schema cluster only
# accumulates the DISTINCTIVE labeled-array labels (key_facts / identifiers /
# dates / amounts) + record kinds — that's the per-type vocabulary crystallization
# (PR3) will promote.
_LEARN_ARRAY_KEYS = ("key_facts", "identifiers", "dates", "amounts")


def _slug_type(s: str | None) -> str | None:
    """Slugify a detected_doc_type into a stable cluster key (snake_case)."""
    s = (s or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    if not s or s in ("other", "unknown", "document", "misc", "general"):
        return None
    return s[:48]


def _learned_labels(args: dict) -> list[str]:
    """The distinctive field labels an extraction found — labeled-array labels."""
    out: list[str] = []
    for arr_key in _LEARN_ARRAY_KEYS:
        out += [i.get("label") for i in (args.get(arr_key) or [])
                if isinstance(i, dict) and i.get("label")]
    return out


def _training_examples(db, owner_pk, args: dict) -> dict | None:
    """Move-1 (b) · a {label: [values]} map for typed schema learning — returned ONLY
    when the owner is a training-eligible (consented free-plan) user. None for paid
    users, so their VALUES never feed the learning loop (names-only stays default)."""
    if not owner_pk or not isinstance(args, dict):
        return None
    try:
        from app.db import get_current_tenant as _tid
        from app.orm import User
        from app.services import consent as _consent
        from app.services import subscriptions as _subs
        u = db.get(User, owner_pk)
        if u is None or _subs.effective_plan(u) != "free":
            return None
        if not _consent.has_current(db, tenant_id=_tid(), user_id=owner_pk,
                                    kind=_consent.KIND_MODEL_TRAINING):
            return None
    except Exception:  # noqa: BLE001
        return None
    ex: dict = {}
    for arr_key in _LEARN_ARRAY_KEYS:
        for item in (args.get(arr_key) or []):
            if isinstance(item, dict) and item.get("label") and item.get("value") not in (None, ""):
                ex.setdefault(item["label"], []).append(str(item["value"]))
    return ex or None


def _augment_schema_fields(base_fields: dict, promoted: dict | None) -> dict:
    """Move-1 PR3b · merge a crystallized schema's promoted fields onto the
    universal base WITHOUT shadowing any base field. Pure. Returns a new dict."""
    merged = dict(base_fields)
    for lab, spec in (promoted or {}).items():
        if lab and lab not in merged and isinstance(spec, dict):
            merged[lab] = spec
    return merged


# The classifier's doc_type vocabulary and the schema-library type_slug taxonomy were built
# separately, so they diverge. These aliases bridge the confident ones; the token-fallback below
# handles the rest. Types with NO reasonable schema (resume, astrology_report, shopping_list…) stay
# universal on purpose — the taxonomy simply doesn't cover them (generate a schema to fix that).
_SLUG_ALIASES = {
    "business_profile": "business_registration",
    "master_service_agreement": "contract",
    "service_agreement": "contract",
    "id_document": "national_id",
    "identity_document": "national_id",
    "identity_card": "national_id",
    "travel_authorization_document": "passport",
    "customer_payment": "invoice",
}
# doc_type tokens too generic to disambiguate a slug on their own.
_WEAK_SLUG_TOKENS = {"report", "statement", "document", "form", "letter", "note", "card",
                     "certificate", "agreement", "record", "profile", "the", "of"}


def _resolve_schema_slug(db: Session, doc_type: str) -> str | None:
    """Map a classifier doc_type to the closest APPROVED schema type_slug: exact → alias →
    distinctive-token (a doc_type token that appears in EXACTLY ONE approved slug). Returns None
    when nothing confidently matches — the doc then stays on the universal extractor."""
    from sqlalchemy import select as _select
    from app.db import get_current_tenant
    from app.orm import SchemaLibrary
    slugs = set(db.scalars(_select(SchemaLibrary.type_slug).where(
        SchemaLibrary.tenant_id == get_current_tenant(),
        SchemaLibrary.status == "approved")).all())
    if not slugs:
        return None
    dt = (doc_type or "").lower().strip()
    if dt in slugs:
        return dt
    if dt in _SLUG_ALIASES and _SLUG_ALIASES[dt] in slugs:
        return _SLUG_ALIASES[dt]
    for tok in dt.split("_"):
        if tok in _WEAK_SLUG_TOKENS or len(tok) < 4:
            continue
        matches = [s for s in slugs if tok in s.split("_")]
        if len(matches) == 1:
            return matches[0]
    return None


def _library_schema(db: Session, doc_type: str) -> "FactSchema | None":
    """An APPROVED schema_library entry for this type, as a FactSchema. It overrides the built-in
    routing AND the documents-product universal-force, so a HITL-approved per-type schema is what
    actually gets used. Resolves the doc_type to the nearest approved slug (exact/alias/token).
    Latest approved version wins. Best-effort — never breaks extraction."""
    if not doc_type:
        return None
    try:
        from sqlalchemy import select as _select
        from app.db import get_current_tenant
        from app.orm import SchemaLibrary
        slug = _resolve_schema_slug(db, doc_type)
        if not slug:
            return None
        row = db.scalar(_select(SchemaLibrary).where(
            SchemaLibrary.tenant_id == get_current_tenant(),
            SchemaLibrary.type_slug == slug,
            SchemaLibrary.status == "approved",
        ).order_by(SchemaLibrary.version.desc()))
        if row and isinstance(row.fields, dict) and row.fields:
            return FactSchema(label=row.label,
                              description=row.description or f"Extract a {row.label}.",
                              fields=row.fields)
    except Exception as e:  # noqa: BLE001
        log.warning("fact_extractor: library-schema lookup failed for %r: %s", doc_type, e)
    return None


# Built-in curated schemas trusted enough to run in the DOCUMENTS product despite the
# universal-force — reserved for types whose structure universal provably loses. `resume`
# is the first: universal collapses the whole academic history into one `highest_education`
# key_fact, dropping the individual SSC/HSC/degree rows. A mis-classified non-résumé routed
# here is still rescued by the type-mismatch → universal retry below.
_DOCUMENTS_BUILTIN_SCHEMAS = {"resume"}

def would_use_curated_schema(db: Session, doc_type: str) -> bool:
    """True if extract() would route this doc_type to a NON-universal schema — an
    approved library schema, an allow-listed built-in in the documents product, or
    any built-in route in the audit product. Mirrors the routing in extract() WITHOUT
    an LLM call. Used by the type-reconciler to decide whether a doc whose type was
    reconciled from 'other' needs a re-extract with its now-curated schema."""
    if not doc_type:
        return False
    if _library_schema(db, doc_type) is not None:
        return True
    key = DOC_TYPE_TO_SCHEMA.get(doc_type)
    if not key or key == "universal":
        return False
    settings = get_settings()
    if settings.product == "documents" and is_enabled("documents_universal_extractor", True):
        return key in _DOCUMENTS_BUILTIN_SCHEMAS
    return True


# Résumé marks-rescue · DashScope/qwen reliably reads education rows (institution/level/
# year) but keeps dropping the printed marks from each row's `score`, even when the résumé
# clearly prints "percentage: 61.47%" / "CGPA: 6.35/10.00". Each row's year-range is unique,
# so we re-attach the mark deterministically: find the row's year (or institution) in the doc
# text and read the labelled grade that follows it. Never overwrites a score the model DID give.
_MARK_CGPA_RX = re.compile(r"(?i)\b(cgpa|gpa)\b\s*[:=]?\s*([0-9]{1,2}(?:\.[0-9]+)?(?:\s*/\s*[0-9]{1,2}(?:\.[0-9]+)?)?)")
_MARK_PCT_RX = re.compile(r"(?i)(?:percentage|percent|marks?|score)?\s*[:=]?\s*([0-9]{1,3}(?:\.[0-9]+)?)\s*%")

# Résumé key normalisation · qwen ignores the tool schema's property names and emits its own
# (degree / languages_known / mobile / …), so column headers drift between résumés. Fold the
# common variants onto the schema's canonical keys. ONLY known aliases are renamed — unknown
# keys (projects, participations, hobbies, …) pass through untouched, so no data is dropped.
_RESUME_KEY_ALIASES = {
    "name": "full_name", "candidate_name": "full_name",
    "email_address": "email", "e_mail": "email", "mail": "email",
    "mobile": "phone", "mobile_number": "phone", "contact": "phone",
    "contact_number": "phone", "phone_number": "phone",
    "objective": "headline", "summary": "headline", "professional_summary": "headline",
    "career_objective": "headline", "profile_summary": "headline",
    "languages_known": "languages", "known_languages": "languages",
    "work_experience": "experience", "employment": "experience",
    "employment_history": "experience", "work_history": "experience",
}
_EDU_KEY_ALIASES = {
    "degree": "qualification", "level": "qualification", "course": "qualification",
    "exam": "qualification", "qualification_name": "qualification",
    "institute": "institution", "school": "institution", "college": "institution",
    "university": "institution", "board": "institution", "institution_name": "institution",
    "stream": "field_of_study", "major": "field_of_study", "branch": "field_of_study",
    "specialization": "field_of_study", "specialisation": "field_of_study",
    "passing_year": "year", "year_of_passing": "year", "completion_year": "year",
    "duration": "year", "years": "year",
    "grade": "score", "percentage": "score", "marks": "score",
    "cgpa": "score", "gpa": "score", "result": "score",
}
_EXP_KEY_ALIASES = {
    "company": "organization", "company_name": "organization",
    "employer": "organization", "organisation": "organization",
    "role": "title", "position": "title", "designation": "title", "job_title": "title",
    "from": "start_date", "start": "start_date", "from_date": "start_date",
    "to": "end_date", "end": "end_date", "to_date": "end_date",
    "responsibilities": "summary", "description": "summary", "details": "summary",
}


def _rename_keys(obj: dict, aliases: dict) -> dict:
    """Return a copy of `obj` with known variant keys renamed to their canonical names.
    On a collision, keep the first non-empty value so a blank variant can't clobber good
    data. Unknown keys pass through unchanged; insertion order is otherwise preserved."""
    if not isinstance(obj, dict):
        return obj
    out: dict = {}
    for k, v in obj.items():
        canon = aliases.get(k, k)
        if canon in out:
            if not str(out[canon] or "").strip() and str(v or "").strip():
                out[canon] = v
        else:
            out[canon] = v
    return out


def _normalize_resume_keys(args: dict) -> None:
    """In-place · fold qwen's free-form résumé keys onto the schema's canonical names so the
    Fields view renders stable labels/columns across résumés. Best-effort; renames only known
    aliases at the top level and inside education[] / experience[] rows."""
    if not isinstance(args, dict):
        return
    top = _rename_keys(args, _RESUME_KEY_ALIASES)
    args.clear()
    args.update(top)
    edu = args.get("education")
    if isinstance(edu, list):
        args["education"] = [_rename_keys(r, _EDU_KEY_ALIASES) if isinstance(r, dict) else r for r in edu]
    exp = args.get("experience")
    if isinstance(exp, list):
        args["experience"] = [_rename_keys(r, _EXP_KEY_ALIASES) if isinstance(r, dict) else r for r in exp]


def _rescue_resume_scores(text: str, args: dict) -> None:
    """In-place · fill empty education[].score from the doc text by anchoring on each row's
    year (fallback: institution). Best-effort — silently no-ops on any miss. Documents-safe:
    only labelled grades (CGPA/percentage/%) within ~70 chars after the anchor are used, so a
    stray number elsewhere on the page can't be mis-attributed."""
    edu = args.get("education")
    if not isinstance(edu, list) or not text:
        return
    flat = re.sub(r"\s+", " ", text)
    for row in edu:
        if not isinstance(row, dict):
            continue
        if str(row.get("score") or "").strip():
            continue  # keep whatever the model captured
        anchor = str(row.get("year") or "").strip() or str(row.get("institution") or "").strip()
        if not anchor or len(anchor) < 4:
            continue
        anchor_flat = re.sub(r"\s+", " ", anchor)
        pos = flat.find(anchor_flat)
        if pos < 0:
            continue
        window = flat[pos + len(anchor_flat): pos + len(anchor_flat) + 70]
        m = _MARK_CGPA_RX.search(window)
        if m:
            row["score"] = f"{m.group(2)} {m.group(1).upper()}"
            continue
        m = _MARK_PCT_RX.search(window)
        if m:
            row["score"] = f"{m.group(1)}%"


# A curated/approved schema that clearly doesn't fit the document scores itself
# at/below this (the prompt tells it to: "if clearly a different type, set
# _doc_confidence < 0.4 and leave fields blank" — but the model often lands
# exactly on 0.4). At/below it, or on a mostly-empty result, we retry universal.
_TYPE_MISMATCH_CONFIDENCE = 0.4


def _scalar_fields(fields: dict) -> list:
    """Top-level fields the mismatch check weighs (skip the _doc_confidence/_notes
    meta keys and repeating-row arrays, which are counted by presence not fill)."""
    return [(k, v) for k, v in (fields or {}).items() if not k.startswith("_")]


def _filled_count(fields: dict) -> int:
    return sum(1 for _, v in _scalar_fields(fields) if v not in ("", [], None, {}))


def _mostly_empty(fields: dict) -> bool:
    """True when a curated-schema extraction filled <= a quarter of its fields — the
    signature of a schema forced onto the wrong document type (mostly-blank output)."""
    fs = _scalar_fields(fields)
    if not fs:
        return True
    return _filled_count(fields) <= max(1, len(fs) // 4)


def extract(db: Session, *, document_pk: int, classifier_doc_type: str,
            _force_universal: bool = False) -> ExtractionResult | None:
    """Run the text-based fact extractor on a document.

    Routes from the classifier's doc_type to the matching schema, sends the
    intro+tail of the document's chunks to the LLM, parses the tool-call
    output, and returns an ExtractionResult.

    Returns None if:
    - No OpenRouter key configured
    - classifier_doc_type doesn't map to any schema
    - The document has no chunks
    - The LLM returned no tool_call output
    """
    settings = get_settings()

    schema_key = DOC_TYPE_TO_SCHEMA.get(classifier_doc_type)
    # HITL-approved library schema wins — it overrides the built-in routing AND the documents-
    # product universal-force below, so an approved per-type schema (invoice, passport, …) is
    # what actually gets used. Inert until a schema is approved for this type.
    _lib_schema = None if _force_universal else _library_schema(db, classifier_doc_type)
    if _lib_schema is not None:
        schema = _lib_schema
        schema_key = classifier_doc_type
        log.info("fact_extractor: using APPROVED library schema for %r · doc pk=%s",
                 classifier_doc_type, document_pk)
    else:
        # M46 · Documents product · ALWAYS use the universal-adaptive schema. It's
        # type-agnostic, so a mis-classification can never route a doc to the wrong
        # curated schema (the audit product keeps its curated dispatch below).
        # EXCEPTION · a small allow-list of built-in schemas whose structure universal
        # provably drops (résumé education rows → a single key_fact). These stay routed
        # to their curated schema; a mis-route is still caught by the type-mismatch
        # retry (conf<0.4 / mostly-empty → universal) below, so the safety net holds.
        if (settings.product == "documents" and is_enabled("documents_universal_extractor", True)
                and schema_key not in _DOCUMENTS_BUILTIN_SCHEMAS):
            if schema_key not in (None, "universal"):
                log.info("fact_extractor: documents product → universal-adaptive (was %s) doc pk=%s",
                         schema_key, document_pk)
            schema_key = "universal"
        if not schema_key:
            # M44.P8 · fall back to the universal extractor instead of skipping.
            # The LLM identifies the doc type itself and slots its content into
            # typed arrays (parties / dates / amounts / identifiers / key_facts).
            # This is what lets the system handle ARBITRARY doc types out of
            # the box (mortgages, K-1s, portfolio statements, lease agreements,
            # hospital bills, ...) without needing a new curated schema for each.
            log.info(
                "fact_extractor: doc_type=%r has no curated schema · "
                "routing to UNIVERSAL extractor",
                classifier_doc_type,
            )
            schema_key = "universal"
        schema = SCHEMAS[schema_key]

    if _force_universal:  # type-mismatch retry — bypass curated/approved routing
        schema_key, schema = "universal", SCHEMAS["universal"]

    # Read the WHOLE doc for the documents product (per-user docs — statements, invoices,
    # multi-page reports) so long docs don't lose their MIDDLE rows to the intro+tail window.
    # This covers built-in curated schemas too (e.g. credit_card_statement / bank_statement),
    # which previously only saw the first-5 + last-5 chunks and dropped page-2/3 transactions.
    # The audit product keeps the windowed default unless there's an approved library schema.
    _full = (_lib_schema is not None) or (settings.product == "documents")
    text, refs = _build_text_excerpt(db, document_pk, full=_full)
    if not text.strip():
        log.warning("fact_extractor: doc pk=%s has no text chunks; skipping", document_pk)
        return None

    # M46 + Move-1 PR1 · self-learning · hint this extraction with what prior
    # documents of the same TYPE taught us. The learning cluster is keyed by the
    # precise (self-labeled) detected_doc_type — but we don't have that until
    # AFTER extraction, so pre-extraction we PREDICT the cluster by embedding the
    # doc's intro and matching it against prior docs' learned-type centroids
    # (owner-scoped, no LLM). Falls back to the coarse classifier type on a cold
    # start. `_intro_emb` + `_owner_pk` are reused post-extraction to fold this
    # doc into the cluster centroid so the NEXT doc of the kind predicts it.
    learned_hint = ""
    hint_key = classifier_doc_type  # the predicted learning cluster (refined below)
    _owner_pk = None
    _intro_emb: list[float] | None = None
    if _full:
        try:
            _drow = docs_repo.get_row_by_pk(db, document_pk)
            _owner_pk = getattr(_drow, "owner_user_id", None)
        except Exception:  # noqa: BLE001
            _owner_pk = None
        try:
            from app.embeddings import embed as _embed
            _v = _embed([text[:4000]])
            _intro_emb = list(_v[0]) if _v else None
        except Exception:  # noqa: BLE001
            _intro_emb = None
        try:
            from app.repositories import learned_schemas as _ls
            if _owner_pk is not None and _intro_emb is not None:
                from app.documents_scope import (
                    get_current_owner_user_pk, set_current_owner_user_pk,
                )
                from app.repositories import learned_doc_types as _ldt
                _prev = get_current_owner_user_pk()
                set_current_owner_user_pk(_owner_pk)
                try:
                    m = _ldt.match_centroid(db, _intro_emb, settings.centroid_match_threshold)
                finally:
                    set_current_owner_user_pk(_prev)
                if m is not None:
                    hint_key = m[0]  # predicted precise cluster slug
            learned_hint = _ls.hint_for(db, hint_key)
        except Exception:  # noqa: BLE001
            learned_hint = ""
    user_content = f"Document excerpts:\n\n{text}\n\nCall record_doc_facts now."
    if learned_hint:
        user_content = f"{learned_hint}\n\n{user_content}"

    # Move-1 PR3b · adopt the crystallized schema for this cluster — promote its
    # learned labels to first-class fields on the universal tool so the extractor
    # returns them as named top-level values (every flat-key consumer reads those
    # first). Uses the cluster predicted above; base fields are never shadowed.
    # Gated on the crystallization flag; best-effort.
    if _full and settings.schema_crystallize_enabled:
        try:
            from app.repositories import generated_schemas as _gs
            promoted = _gs.active_fields_for(db, hint_key)
            if promoted:
                merged = _augment_schema_fields(schema.fields, promoted)
                if len(merged) > len(schema.fields):
                    schema = FactSchema(label=schema.label, description=schema.description,
                                        fields=merged)
                    log.info("fact_extractor: adopted %d crystallized field(s) for cluster %r doc pk=%s",
                             len(merged) - len(SCHEMAS["universal"].fields), hint_key, document_pk)
        except Exception:  # noqa: BLE001 — adoption is best-effort
            pass

    from app.llm.gateway import call as gateway_call, Message as GatewayMessage

    model = _active_model()
    msgs = [
        GatewayMessage(role="system", content=get_prompt("extraction", schema_label=schema.label)),
        GatewayMessage(role="user", content=user_content),
    ]
    tools = [schema.to_openrouter_tool()]
    tool_choice = {"type": "function", "function": {"name": "record_doc_facts"}}
    # Deterministic extraction — without temperature=0 the provider default (~0.7) made the SAME
    # doc yield different row counts run-to-run. Extraction is a read task; it must be repeatable.
    # 4096 covers ~100 transactions safely; full mode reads the whole doc → allow more rows out.

    log.info(
        "fact_extractor: dispatching doc pk=%s classifier_type=%s → schema=%s, %d chunks  model=%s",
        document_pk, classifier_doc_type, schema_key, len(refs), model,
    )

    import time as _time
    t0 = _time.perf_counter()
    try:
        result = gateway_call(
            model=model, messages=msgs, temperature=0,
            max_tokens=8192 if _full else 4096,
            tools=tools, tool_choice=tool_choice,
            tenant_id=settings.tenant_id,
            task_kind="extract",
        )
    except Exception as e:
        log.warning("fact_extractor: LLM call failed: %s", e)
        ledger.record_call(
            db, task="extract", tier="t2", provider="?", model=model,
            document_pk=document_pk,
            status="failed", error=str(e),
            latency_ms=int((_time.perf_counter() - t0) * 1000),
        )
        return None
    ledger.record_call(
        db, task="extract", tier="t2", provider=result.provider, model=result.model,
        document_pk=document_pk,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        latency_ms=result.latency_ms,
        status="ok",
    )

    # Parse tool-call output (OpenAI-compatible shape). LLMs sometimes emit
    # JSON with unescaped quotes inside string values when packing dozens of
    # transactions — strict json.loads then fails. Fall back to json-repair
    # which patches the common failure modes (unescaped quotes, trailing
    # commas, missing closing braces) without losing data.
    try:
        tool_calls = result.tool_calls or []
        if not tool_calls:
            log.warning("fact_extractor: no tool_calls in response; raw=%r", result.text)
            return None
        args_str = tool_calls[0]["function"]["arguments"]
        try:
            args = json.loads(args_str)
        except json.JSONDecodeError as je:
            try:
                from json_repair import repair_json
                repaired = repair_json(args_str)
                args = json.loads(repaired) if isinstance(repaired, str) else repaired
                log.info(
                    "fact_extractor: strict JSON parse failed (%s) — json-repair recovered the payload",
                    je,
                )
                # T1.3 · Detect truncation. json-repair will sometimes return
                # a structurally-valid but TRUNCATED array — a 100-transaction
                # bank statement could come back with 47 entries because one
                # description had an unescaped quote on item 48 and the lib
                # dropped the rest. Spot-check by counting top-level `{`
                # occurrences in arrays vs the parsed array lengths, and try
                # a per-item salvage when divergence is large.
                args = _salvage_truncated_arrays(args, args_str)
            except Exception as re:  # noqa: BLE001
                log.warning(
                    "fact_extractor: failed to parse tool-call output even with json-repair: strict=%s repair=%s",
                    je, re,
                )
                return None
    except (KeyError, IndexError) as e:
        log.warning("fact_extractor: failed to read tool-call output: %s", e)
        return None

    # The model (or json-repair salvage) can return a top-level LIST instead of the
    # expected object; `.pop("_doc_confidence")` on a list raises TypeError (str
    # index), which — being outside the parse try/except — would 500 the extraction
    # API. Treat a non-dict as an unusable extraction.
    if not isinstance(args, dict):
        log.warning("fact_extractor: tool-call output was %s, not an object", type(args).__name__)
        return None

    confidence = float(args.pop("_doc_confidence", 0.0))
    notes = str(args.pop("_notes", ""))

    # Type-mismatch fallback. A curated/approved schema forced onto the wrong kind of
    # document (a mis-classification: e.g. a portfolio-transfer letter → `invoice`, a
    # trainer profile → `training_certificate`) scores itself low and returns mostly-
    # blank fields. Rather than persist an empty extraction, retry with the type-
    # agnostic UNIVERSAL extractor so we still capture the doc's real content
    # (parties/dates/amounts/identifiers/key_facts). Trigger on the confidence signal
    # (<=, since the model often lands exactly on the 0.4 boundary) OR on a mostly-empty
    # result (the actual symptom — a curated schema that couldn't fill its fields).
    # Universal never retries (schema_key == "universal" → no loop); one extra LLM call.
    # Fill-rate is the reliable symptom (confidence is noisy — the same doc scored 0.4
    # then 0.8 across runs while filling only 1/11 fields both times), so a mostly-empty
    # curated result triggers the fallback regardless of the confidence number.
    if (not _force_universal and schema_key != "universal"
            and (confidence <= _TYPE_MISMATCH_CONFIDENCE or _mostly_empty(args))):
        log.info("fact_extractor: schema %r scored %.2f + %d/%d fields filled (likely wrong "
                 "type) for doc pk=%s · retrying with the UNIVERSAL extractor",
                 schema_key, confidence, _filled_count(args), len(_scalar_fields(args)), document_pk)
        uni = extract(db, document_pk=document_pk, classifier_doc_type=classifier_doc_type,
                      _force_universal=True)
        if uni is not None:
            return uni
        # universal produced nothing usable → keep the (blank) schema result below

    # M46 · Documents product · self-critique completeness pass. A second LLM
    # call reviews the doc against what we extracted and appends anything the
    # first pass missed (records / parties / dates / amounts / identifiers /
    # key_facts). Type-agnostic; documents-gated so audit is unchanged.
    # M49 · cost: only run the second (full-doc) completeness pass when the doc is
    # long enough to plausibly have dropped rows — short docs almost never benefit,
    # so this halves extraction cost on the typical 1–2 page personal doc.
    # Run the completeness/reconciliation pass whenever the doc has repeating ROWS (transactions,
    # line_items, holdings, records) — NOT just the universal schema. Statements/invoices with a
    # curated schema were previously skipped, so their middle-page rows were never recovered.
    _row_keys = _array_of_objects_keys(args) if isinstance(args, dict) else []
    _recs = args.get("records") if isinstance(args, dict) else None
    _verify_worth_it = bool(_row_keys) or (len(text) >= 8000) or (isinstance(_recs, list) and len(_recs) >= 5)
    # P2 · cloud-only — OSS deployments skip verify, single-pass only.
    from app.license import is_cloud
    if (is_cloud() and settings.product == "documents"
            and is_enabled("documents_extract_verify", True) and _verify_worth_it):
        try:
            args = _self_verify(db, document_pk, text, args, settings)
        except Exception:  # noqa: BLE001 — verification is best-effort
            log.warning("fact_extractor: self-verify pass failed for doc pk=%s (kept first pass)", document_pk)

    # Résumé · normalise qwen's free-form keys onto canonical names (stable columns), THEN
    # deterministically re-attach the education marks the model dropped (year-anchored). Order
    # matters: the rescue reads the canonical `year`/`score` keys the normaliser produces.
    if schema_key == "resume" and isinstance(args, dict):
        try:
            _normalize_resume_keys(args)
            _rescue_resume_scores(text, args)
        except Exception:  # noqa: BLE001 — best-effort enrichment
            log.warning("fact_extractor: résumé post-processing failed for doc pk=%s", document_pk)

    # M46 + Move-1 PR1 · self-learning · record this doc's DISTINCTIVE fields +
    # record kinds under the PRECISE detected_doc_type cluster (not the coarse
    # classifier type), and fold the doc into that cluster's centroid so the next
    # document of the kind predicts it pre-extraction. Cluster key falls back to
    # the classifier type when the extractor didn't self-label.
    if _full:
        try:
            from app.repositories import learned_schemas as _ls
            cluster_key = _slug_type(args.get("detected_doc_type") if isinstance(args, dict) else None) \
                or classifier_doc_type
            labels = _learned_labels(args)
            kinds = [r.get("kind") for r in (args.get("records") or [])
                     if isinstance(r, dict) and r.get("kind")]
            # Value examples for typed crystallization — consented free docs only.
            _examples = _training_examples(db, _owner_pk, args)
            _ls.record(db, cluster_key, labels, kinds, examples=_examples)
            # Fold into the learned-type centroid (owner-scoped) so future docs'
            # pre-extraction prediction can find this cluster. Reuses the intro
            # embedding computed above; no extra LLM/embed call.
            if _owner_pk is not None and _intro_emb is not None:
                from app.documents_scope import (
                    get_current_owner_user_pk, set_current_owner_user_pk,
                )
                from app.repositories import learned_doc_types as _ldt
                _prev = get_current_owner_user_pk()
                set_current_owner_user_pk(_owner_pk)
                try:
                    _ldt.register(db, cluster_key, cluster_key.replace("_", " ").title(), source="ai")
                    _ldt.update_centroid(db, cluster_key, _intro_emb)
                finally:
                    set_current_owner_user_pk(_prev)
        except Exception:  # noqa: BLE001 — learning is best-effort
            log.warning("fact_extractor: learned-schema record failed for doc pk=%s", document_pk)

    field_bboxes = _locate_field_bboxes(db, document_pk, args, refs)
    log.info(
        "fact_extractor: pinned tight bboxes for %d/%d scalar fields on doc pk=%s",
        len(field_bboxes), sum(1 for v in args.values() if isinstance(v, str) and v.strip()), document_pk,
    )

    # G4 · derive per-field confidence from grounding + value plausibility +
    # the whole-doc prior, so the review UI can surface uncertain fields.
    from app import field_confidence as _field_conf
    field_conf = _field_conf.score_fields(args, field_bboxes, confidence)

    return ExtractionResult(
        schema_key=schema_key,
        fields=args,
        confidence=confidence,
        notes=notes,
        model=_active_model(),
        chunk_refs=refs,
        field_bboxes=field_bboxes,
        raw_response={},
        field_confidence=field_conf,
    )
