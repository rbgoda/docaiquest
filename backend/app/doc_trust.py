"""Document trust score — unified extraction-confidence triage.

Pure-stdlib (offline-testable). Combines the three confidence signals the
pipeline already produces into ONE 0..1 per-document trust score + reasons, so a
user with 100s of documents can answer "which ones should I double-check?":

  * classification confidence (`doc_type_confidence`)
  * OCR page quality (G3 `ocr_quality`)
  * per-field extraction confidence (G4 `field_confidence`)
  * ingestion status

This is a differentiator beyond pure parsing infrastructure: parsing tells you
*what the document says*; the trust score tells you *how much to believe it* and
*where to look*. It powers a single "needs review" queue across the workspace.

FRAMING (important): the score is *extraction-confidence triage*, NOT a measured
accuracy, and it must never be shown as "N% accurate" for a document that produced
no structured fields. A document can be parsed perfectly (clean OCR, full text) yet
carry no schema — because its type isn't classified/supported, not because anything
was extracted wrongly. `state` names that distinction so the UI shows the right
message (and the right next action):

  * unprocessed  — not ready / ingestion failed (content not captured)
  * unstructured — parsed OK but no structured fields → offer full text / Markdown,
                   NOT "low accuracy". There is nothing to be accurate *about*.
  * needs_review — has structured fields, but some are low-confidence → verify them
  * review       — medium confidence
  * trusted      — high confidence
"""
from __future__ import annotations

from app.field_confidence import LOW_CONFIDENCE, low_confidence_fields

# Trust bands.
HIGH, MEDIUM = 0.8, 0.6


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _inner_field_confidence(extracted_fields) -> dict:
    """`extracted_fields` is `{doc_type, fields, field_confidence, ...}` — pull
    the per-field confidence map (G4)."""
    if not isinstance(extracted_fields, dict):
        return {}
    fc = extracted_fields.get("field_confidence")
    return fc if isinstance(fc, dict) else {}


def _has_structured_fields(extracted_fields) -> bool:
    """True when the extractor produced structured output to be *accurate about*.
    A doc with no `fields` (e.g. unclassified/unsupported type) is `unstructured` —
    its content may be fully captured; it just carries no schema."""
    if not isinstance(extracted_fields, dict):
        return False
    f = extracted_fields.get("fields")
    return bool(f) if isinstance(f, (dict, list)) else False


def document_trust(*, ingestion_status: str | None, doc_type: str | None,
                   doc_type_confidence: float | None, ocr_quality: dict | None,
                   extracted_fields: dict | None, review_status: str | None = None) -> dict:
    """Return {score, level, state, reasons:[...]}. `level` ∈ {high, medium, low};
    `state` ∈ {unprocessed, unstructured, needs_review, review, trusted, verified} (see module
    docstring — `state` is what the UI should label, never a bare "N% accuracy")."""
    reasons: list[str] = []

    if (ingestion_status or "") == "failed":
        return {"score": 0.0, "level": "low", "state": "unprocessed", "reasons": ["ingestion_failed"]}
    if (ingestion_status or "") != "ready":
        return {"score": 0.0, "level": "low", "state": "unprocessed", "reasons": ["not_ready"]}

    # A human sign-off is ground truth — an approved doc is VERIFIED, not the model's estimate.
    # (Resolves "I reviewed it and it's correct, but it still shows 90%/75%".)
    if (review_status or "") == "reviewed":
        return {"score": 1.0, "level": "high", "state": "verified", "reasons": []}

    score = float(doc_type_confidence) if doc_type_confidence is not None else 0.7
    if not doc_type or doc_type == "unclassified":
        score -= 0.25
        reasons.append("unclassified")
    elif doc_type_confidence is not None and float(doc_type_confidence) < LOW_CONFIDENCE:
        reasons.append("low_classification_confidence")

    # G3 · OCR quality.
    if isinstance(ocr_quality, dict) and ocr_quality.get("flagged"):
        n = int(ocr_quality.get("lowConfidencePages") or 0)
        score -= min(0.3, 0.1 + 0.05 * n)
        reasons.append(f"low_ocr_confidence:{n}_pages")

    # G4 · per-field confidence (extracted-but-uncertain fields).
    fconf = _inner_field_confidence(extracted_fields)
    if fconf:
        uncertain = low_confidence_fields(fconf)
        if uncertain:
            score -= min(0.3, 0.05 * len(uncertain))
            reasons.append(f"uncertain_fields:{len(uncertain)}")

    score = round(_clamp(score), 3)
    level = "high" if score >= HIGH else "medium" if score >= MEDIUM else "low"

    # State separates FRAMING from the raw score. No structured fields → `unstructured`
    # (content captured, no schema) — the UI offers full text / Markdown instead of
    # presenting the score as accuracy. Otherwise the score IS extraction confidence.
    if not _has_structured_fields(extracted_fields):
        state = "unstructured"
    elif level == "high":
        state = "trusted"
    elif level == "medium":
        state = "review"
    else:
        state = "needs_review"
    return {"score": score, "level": level, "state": state, "reasons": reasons}


def needs_review(trust: dict) -> bool:
    """True when a document should be surfaced in the review queue."""
    return (trust or {}).get("level") == "low"
