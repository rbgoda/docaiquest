"""KYC field extractor — pulls typed fields per document type using
Anthropic vision (via OpenRouter, reusing the existing API key).

Architecture
------------
The matcher (M11) already identifies what TYPE a document is — passport,
Aadhaar, utility bill, etc. — with confidence. This extractor runs AFTER
the matcher attaches a doc to a KYC-* requirement, and pulls the
document-type-specific structured fields (name, DOB, ID number, expiry,
address, etc.) into JSON.

It works on both PDFs (text-extractable layer) and images (JPG/PNG/HEIC),
so phone-photos of passports go through the same path as digital docs.

Why Anthropic vision (claude-haiku-4.5)
---------------------------------------
- Best-in-class for ID-card OCR + structured field extraction in one call
- ~$0.003/page input + ~$0.001 output → ~$4 per 1,000 docs (very affordable)
- Reuses the existing OpenRouter key (`DOCAIQ_OPENROUTER_API_KEY`)
- Returns clean JSON via tool-use, no fragile prompt-parsing

Schemas per doc-type
--------------------
Each `KycDocType` has a tool-input schema that pins down the exact fields
to extract. Adding new doc-types is a one-stanza change (new entry in
SCHEMAS) — no other code touches.
"""

from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx

from app.config import get_settings
from app.llm.prompts import get_prompt
from app.storage import get_object_bytes

log = logging.getLogger(__name__)


# ── Doc-type ↔ requirement-id mapping ─────────────────────────────────────
# When the matcher attaches a doc to one of these KYC-* requirement IDs,
# the worker enqueues an extraction job. The value is the doc-type key
# into SCHEMAS below.
KYC_REQUIREMENT_TO_DOC_TYPE: dict[str, str] = {
    "KYC-ID-01": "primary_photo_id",
    "KYC-ID-02": "address_proof",
    "KYC-US-01": "passport_us",
    "KYC-UK-01": "passport_uk",
    "KYC-EU-01": "id_eu",
    "KYC-IN-01": "aadhaar",
    "KYC-IN-02": "pan",
    "KYC-SG-01": "nric",
    "KYC-AU-01": "id_au",
    "KYC-CA-01": "id_ca",
    "KYC-CN-01": "id_cn",
    "KYC-BR-01": "id_br",
    "KYC-JP-01": "id_jp",
    "AML-SOF-01": "bank_statement",
    "KYB-BIZ-01": "incorporation_cert",
    "KYB-BIZ-03": "tax_id",
}


# Classifier doc_type → schema key.
#
# Used by the reclassify endpoint to route image docs to the KYC extractor
# when the doc isn't attached to a KYC-* requirement (and so the
# requirement-ID-based KYC_REQUIREMENT_TO_DOC_TYPE map can't help). The
# classifier emits human-typed strings ("driver_licence", "passport",
# "bank_statement") that need explicit mapping to our schema keys
# ("primary_photo_id", "passport_us", "bank_statement").
#
# Generic IDs all route to "primary_photo_id" — the country-specific
# schemas (passport_us, aadhaar, nric, …) are only used when the matcher
# attached the doc to a country-scoped KYC requirement (KYC-US-01 etc) so
# we know which variant to pin to. For ad-hoc uploads we use the generic
# schema which covers the same field set without country-specific quirks.
CLASSIFIER_DOC_TYPE_TO_SCHEMA: dict[str, str] = {
    "passport": "primary_photo_id",
    "national_id": "primary_photo_id",
    "driver_licence": "primary_photo_id",
    "utility_bill": "address_proof",
    "bank_statement": "bank_statement",
    "credit_card_statement": "bank_statement",
    "incorporation_certificate": "incorporation_cert",
    "tax_registration": "tax_id",
}


# ── Per-doc-type extraction schemas ───────────────────────────────────────
# Each schema describes a JSON tool-input that Claude will fill in.
# Common fields (holder_name, dob, expiry, country) appear in many schemas
# but as separate top-level keys (no inheritance) so each extractor is a
# self-contained contract.

@dataclass(frozen=True)
class DocTypeSchema:
    label: str                          # human-readable, shown in UI
    description: str                    # tells Claude what to extract
    fields: dict[str, dict[str, Any]]   # JSON schema for the tool input

    def to_anthropic_tool(self) -> dict[str, Any]:
        return {
            "name": "record_kyc_fields",
            "description": self.description,
            "input_schema": {
                "type": "object",
                "properties": {
                    **self.fields,
                    "_doc_confidence": {
                        "type": "number",
                        "description": "Your overall confidence (0.0-1.0) that this document is genuine and the fields are accurate. ≥ 0.85 means high-quality extraction.",
                    },
                    "_notes": {
                        "type": "string",
                        "description": "Brief notes — e.g. 'ID expired', 'photo blurry', 'partial match on holder name'. Empty if nothing unusual.",
                    },
                    # M40 · per-field 2D bounding boxes — same shape as the
                    # Gemini path so the post-extraction converter is shared.
                    # Claude's vision can attempt these for IDs but accuracy
                    # is lower than Gemini's first-class `box_2d`. Best-
                    # effort: missing entries fall through to no-overlay on
                    # the FE legend, and the typed field row still works.
                    "_field_bboxes": {
                        "type": "object",
                        "description": (
                            "OPTIONAL — for each field you extracted, add an "
                            "entry to this object mapping field_name → "
                            "[ymin, xmin, ymax, xmax] in 0-1000 normalized "
                            "coordinate space. Each box must tightly enclose "
                            "the printed value of that field on the document "
                            "image. Omit fields you cannot precisely locate "
                            "(missing is better than wrong)."
                        ),
                    },
                },
                "required": ["_doc_confidence"],
            },
        }


SCHEMAS: dict[str, DocTypeSchema] = {
    # ── Generic individual IDs ──────────────────────────────────────────
    "primary_photo_id": DocTypeSchema(
        label="Government-issued photo ID",
        description="Extract identifying fields from a government-issued photo ID (passport, national ID card, or driver licence — any country).",
        fields={
            "doc_subtype": {"type": "string", "enum": ["passport", "national_id", "driver_licence", "other"]},
            "holder_name": {"type": "string", "description": "Full name as printed on the document."},
            "dob": {"type": "string", "description": "Date of birth in YYYY-MM-DD."},
            "document_number": {"type": "string", "description": "Document number (passport number, licence number, ID number)."},
            "expiry_date": {"type": "string", "description": "Expiry date YYYY-MM-DD. Use 'unknown' if not visible."},
            "issuing_country": {"type": "string", "description": "ISO 3166-1 alpha-2 country code if visible."},
            "issuing_authority": {"type": "string", "description": "Issuing authority / state / agency."},
            "nationality": {"type": "string", "description": "Nationality if explicit on the document."},
        },
    ),
    "address_proof": DocTypeSchema(
        label="Proof of current address",
        description="Extract address-verification fields from a utility bill, bank statement, or government correspondence dated within 90 days.",
        fields={
            "doc_subtype": {"type": "string", "enum": ["utility_bill", "bank_statement", "government_letter", "other"]},
            "provider_name": {"type": "string", "description": "Utility provider, bank, or issuing authority."},
            "holder_name": {"type": "string", "description": "Account holder name."},
            "address": {"type": "string", "description": "Full address as printed."},
            "billing_date": {"type": "string", "description": "Statement / billing date YYYY-MM-DD."},
            "account_number_last_4": {"type": "string", "description": "Last 4 digits of the account / customer number (mask the rest)."},
        },
    ),
    # ── Country-specific passports / IDs ────────────────────────────────
    "passport_us": DocTypeSchema(
        label="US passport or state photo ID",
        description="Extract from a US passport or REAL-ID-compliant state-issued photo ID.",
        fields={
            "doc_subtype": {"type": "string", "enum": ["us_passport", "state_drivers_licence", "state_id_card"]},
            "holder_name": {"type": "string"},
            "dob": {"type": "string", "description": "YYYY-MM-DD"},
            "document_number": {"type": "string"},
            "expiry_date": {"type": "string"},
            "issuing_state": {"type": "string", "description": "Two-letter state code (e.g. CA, NY). Empty for federal passport."},
            "address": {"type": "string", "description": "Address on the licence (state IDs only). Empty for passport."},
        },
    ),
    "passport_uk": DocTypeSchema(
        label="UK passport or driving licence",
        description="Extract from a United Kingdom passport or DVLA photocard driving licence.",
        fields={
            "doc_subtype": {"type": "string", "enum": ["uk_passport", "uk_driving_licence"]},
            "holder_name": {"type": "string"},
            "dob": {"type": "string"},
            "document_number": {"type": "string"},
            "expiry_date": {"type": "string"},
            "address": {"type": "string", "description": "Address on driving licence. Empty for passport."},
        },
    ),
    "id_eu": DocTypeSchema(
        label="EU national ID / eID / passport",
        description="Extract from any EU/EEA member-state national ID card or biometric passport.",
        fields={
            "doc_subtype": {"type": "string", "enum": ["eu_id_card", "eu_passport", "other"]},
            "holder_name": {"type": "string"},
            "dob": {"type": "string"},
            "document_number": {"type": "string"},
            "expiry_date": {"type": "string"},
            "issuing_country": {"type": "string", "description": "ISO 3166-1 alpha-2 (DE, FR, IT, ES, NL, ...)."},
            "nationality": {"type": "string"},
        },
    ),
    "aadhaar": DocTypeSchema(
        label="India · Aadhaar card",
        description="Extract from an Indian Aadhaar card issued by UIDAI.",
        fields={
            "holder_name": {"type": "string"},
            "dob": {"type": "string", "description": "YYYY-MM-DD"},
            "aadhaar_number_last_4": {"type": "string", "description": "ONLY the last 4 digits of the 12-digit Aadhaar number. Mask the rest. UIDAI guidance: never store the full number outside a vault."},
            "gender": {"type": "string", "enum": ["M", "F", "X", "unknown"]},
            "address": {"type": "string"},
        },
    ),
    "pan": DocTypeSchema(
        label="India · PAN card",
        description="Extract from an Indian Permanent Account Number (PAN) card.",
        fields={
            "holder_name": {"type": "string"},
            "father_name": {"type": "string", "description": "Father's / parent's name as printed."},
            "dob": {"type": "string"},
            "pan_number": {"type": "string", "description": "10-character PAN (format AAAAA9999A)."},
        },
    ),
    "nric": DocTypeSchema(
        label="Singapore · NRIC / FIN card",
        description="Extract from Singapore National Registration Identity Card (NRIC) or FIN card.",
        fields={
            "holder_name": {"type": "string"},
            "dob": {"type": "string"},
            "nric_last_4": {"type": "string", "description": "Last 4 chars of the NRIC/FIN (3 digits + check letter). Mask the rest."},
            "card_subtype": {"type": "string", "enum": ["pink_citizen", "blue_pr", "fin_foreigner"]},
            "country_of_birth": {"type": "string"},
        },
    ),
    "id_au": DocTypeSchema(
        label="Australia · driver licence / Medicare / passport",
        description="Extract from an Australian government-issued photo ID.",
        fields={
            "doc_subtype": {"type": "string", "enum": ["au_passport", "au_driver_licence", "au_medicare", "au_proof_of_age"]},
            "holder_name": {"type": "string"},
            "dob": {"type": "string"},
            "document_number": {"type": "string"},
            "expiry_date": {"type": "string"},
            "issuing_state": {"type": "string", "description": "NSW, VIC, QLD, etc. Empty for federal passport / Medicare."},
        },
    ),
    "id_ca": DocTypeSchema(
        label="Canada · passport / provincial ID",
        description="Extract from a Canadian passport or provincial photo ID.",
        fields={
            "doc_subtype": {"type": "string", "enum": ["ca_passport", "ca_provincial_id", "ca_driver_licence"]},
            "holder_name": {"type": "string"},
            "dob": {"type": "string"},
            "document_number": {"type": "string"},
            "expiry_date": {"type": "string"},
            "issuing_province": {"type": "string", "description": "Two-letter province code. Empty for federal passport."},
        },
    ),
    "id_cn": DocTypeSchema(
        label="China · Resident Identity Card",
        description="Extract from a Chinese 居民身份证 (Resident Identity Card).",
        fields={
            "holder_name": {"type": "string"},
            "dob": {"type": "string"},
            "id_number_last_4": {"type": "string", "description": "Last 4 chars of the 18-digit ID number. Mask the rest."},
            "gender": {"type": "string", "enum": ["M", "F", "X", "unknown"]},
            "nationality": {"type": "string"},
            "address": {"type": "string"},
        },
    ),
    "id_br": DocTypeSchema(
        label="Brazil · CPF / RG",
        description="Extract from a Brazilian CPF or RG identity document.",
        fields={
            "doc_subtype": {"type": "string", "enum": ["cpf", "rg", "other"]},
            "holder_name": {"type": "string"},
            "dob": {"type": "string"},
            "cpf_number": {"type": "string", "description": "CPF in format NNN.NNN.NNN-NN if it's a CPF; otherwise empty."},
            "rg_number": {"type": "string", "description": "RG number if visible; otherwise empty."},
            "issuing_state": {"type": "string"},
        },
    ),
    "id_jp": DocTypeSchema(
        label="Japan · My Number / driving licence",
        description="Extract from a Japanese Individual Number Card (My Number) or driver licence.",
        fields={
            "doc_subtype": {"type": "string", "enum": ["my_number_card", "driver_licence"]},
            "holder_name": {"type": "string"},
            "dob": {"type": "string"},
            "document_number_last_4": {"type": "string", "description": "Last 4 chars of the document number. Mask the rest."},
            "expiry_date": {"type": "string"},
            "address": {"type": "string"},
        },
    ),
    # ── AML — source of funds ───────────────────────────────────────────
    "bank_statement": DocTypeSchema(
        label="Bank statement",
        description="Extract from a bank statement covering the customer's source of funds.",
        fields={
            "bank_name": {"type": "string"},
            "account_holder": {"type": "string"},
            "account_number_last_4": {"type": "string", "description": "Last 4 digits of the account number. Mask the rest."},
            "statement_period_start": {"type": "string", "description": "YYYY-MM-DD"},
            "statement_period_end": {"type": "string"},
            "currency": {"type": "string", "description": "ISO 4217 currency code (USD, GBP, INR, ...)."},
            "closing_balance": {"type": "string", "description": "Closing balance as printed."},
            "address_on_statement": {"type": "string"},
        },
    ),
    # ── KYB ─────────────────────────────────────────────────────────────
    "incorporation_cert": DocTypeSchema(
        label="Certificate of incorporation",
        description="Extract from a certificate of incorporation, business registration certificate, or equivalent.",
        fields={
            "company_legal_name": {"type": "string"},
            "registration_number": {"type": "string"},
            "incorporation_date": {"type": "string"},
            "jurisdiction": {"type": "string", "description": "Country / state of incorporation."},
            "registry_name": {"type": "string", "description": "Companies House, Secretary of State, MCA, ACRA, etc."},
            "registered_address": {"type": "string"},
            "company_type": {"type": "string", "description": "LLC, Ltd, GmbH, Pvt Ltd, etc."},
        },
    ),
    "tax_id": DocTypeSchema(
        label="Tax ID / EIN / VAT registration",
        description="Extract from a tax identification document.",
        fields={
            "company_or_holder_name": {"type": "string"},
            "tax_id_number": {"type": "string"},
            "tax_id_type": {"type": "string", "description": "EIN, VAT, GSTIN, TIN, ABN, etc."},
            "issuing_authority": {"type": "string"},
            "issue_date": {"type": "string"},
            "jurisdiction": {"type": "string"},
        },
    ),
}


# ── Vision API call (OpenRouter routing) ─────────────────────────────────
#
# Cascade order — preferred → fallback → last-resort:
#   1. Qwen Vision (qwen/qwen2.5-vl-72b-instruct via OpenRouter)
#       - Excellent vision capability, supports tool_use
#       - Cheap on OpenRouter compared to Claude
#       - User has 1M tokens of OpenRouter budget — effectively free
#   2. Gemini direct (gemini-2.5-flash via Google AI Studio)
#       - First-class box_2d support when it cooperates
#       - Free tier 15 RPM / 1500 RPD, easy to exhaust during QA
#   3. Claude Haiku 4.5 via OpenRouter
#       - Last-resort fallback, paid per call, reliable tool_use
#
# Each model is tried until one returns a successful ExtractionResult.
# OCR augmentation (RapidOCR → Tesseract) runs on whichever model succeeded
# so per-field bboxes are populated regardless of model.
#
# Override the preferred model via env: DOCAIQ_KYC_PREFERRED_MODEL.
# Set to empty string to skip the Qwen step entirely (e.g., if you want
# Gemini→Claude only).

_DEFAULT_PREFERRED_MODEL = "qwen/qwen2.5-vl-72b-instruct"
_ANTHROPIC_VIA_OPENROUTER_MODEL = "anthropic/claude-haiku-4.5"
_OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


def _preferred_model() -> str:
    """Resolve the preferred OpenRouter vision model. Reads
    DOCAIQ_KYC_PREFERRED_MODEL at call time so it survives tenant restarts
    without baking the choice into the image."""
    import os
    val = os.environ.get("DOCAIQ_KYC_PREFERRED_MODEL")
    if val is None:
        return _DEFAULT_PREFERRED_MODEL
    return val.strip()  # empty string → skip Qwen step entirely


def _image_data_url(image_bytes: bytes, mime: str) -> str:
    """Build a data: URL the way OpenAI-compatible vision endpoints want it."""
    b64 = base64.b64encode(image_bytes).decode("ascii")
    return f"data:{mime};base64,{b64}"


def _build_prompt(schema: DocTypeSchema, *, want_bboxes: bool = False) -> str:
    """The user-facing instruction. The tool-input schema does the heavy
    lifting; this prompt just orients the model and sets quality bars.

    When `want_bboxes=True` (Gemini path), additionally ask the model to
    return a `_field_bboxes` map with normalized 2D bounding boxes per
    field so the reviewer-facing UI can draw colored rectangles over the
    exact regions on the document image.
    """
    base = (
        f"You are extracting structured KYC fields from a document.\n\n"
        f"Expected document type: {schema.label}\n\n"
        f"Instructions:\n"
        f"- Look at the image / page content carefully.\n"
        f"- Fill in every field you can read with high confidence.\n"
        f"- Use empty string \"\" (not 'unknown') for fields you cannot read.\n"
        f"- For dates always use YYYY-MM-DD format.\n"
        f"- For IDs that should be partially masked per the schema, return only the last N chars.\n"
        f"- Set _doc_confidence to your overall confidence (0.0–1.0) that the document is genuine and fields are correct.\n"
        f"- If the document is clearly a different type than expected, set _doc_confidence < 0.4 and leave fields blank.\n"
    )
    if want_bboxes:
        base += (
            "\nIn ADDITION, populate `_field_bboxes` with one entry per field "
            "you extracted, where each entry is `[ymin, xmin, ymax, xmax]` in "
            "Gemini's normalized 0-1000 coordinate space — the same 2D "
            "bounding-box format the Gemini API uses for object detection. "
            "The box must tightly enclose the printed value of that field on "
            "the document image. If you cannot locate a field's region "
            "precisely, OMIT that field from `_field_bboxes` (do not "
            "guess — a missing entry is better than a misleading one).\n"
        )
    base += "\nCall the record_kyc_fields tool with your extraction."
    return base


@dataclass
class ExtractionResult:
    doc_type: str           # the schema key used
    fields: dict[str, Any]  # everything the model returned (minus _doc_confidence / _notes)
    confidence: float
    notes: str
    model: str
    raw_response: dict      # the full provider response, kept for debugging
    # M40 · per-field bbox map populated by the Gemini Vision path. Shape:
    #   { field_name → {x0, y0, x1, y1, page_w, page_h} }
    # All values in Gemini's 0-1000 normalized space, so page_w == page_h ==
    # 1000 and the frontend scales by the rendered image dimensions.
    # Empty when (a) running the OpenRouter fallback, (b) Gemini didn't
    # return bboxes for that doc, or (c) parse failed. Frontend handles the
    # empty case gracefully — the field legend still renders, just without
    # the per-field rect overlay on the doc.
    field_bboxes: dict[str, dict[str, Any]] | None = None


def extract(*, s3_key: str, mime: str, doc_type: str) -> ExtractionResult | None:
    """Run the vision-based extractor on an object in MinIO.

    M31.8 · Prefers Gemini Vision direct when DOCAIQ_GOOGLE_GENAI_API_KEY
    is set (better accuracy on diverse IDs, free tier 15 RPM, no
    OpenRouter middleman). Falls back to Anthropic-via-OpenRouter when
    only OpenRouter key is configured.

    Returns ExtractionResult on success, None on configuration failure
    (no key, unknown doc_type, etc.). Raises on transport errors so
    the worker can retry."""
    settings = get_settings()
    if not (settings.google_genai_api_key or settings.openrouter_api_key):
        log.warning("kyc_extractor: no LLM key set; skipping extraction")
        return None
    if doc_type not in SCHEMAS:
        log.warning("kyc_extractor: unknown doc_type %s; skipping", doc_type)
        return None
    schema = SCHEMAS[doc_type]

    # Pull the bytes from MinIO. For PDFs we send the first page only; for
    # images we send as-is. (Multi-page docs route the first page through
    # vision; if more pages matter, we can extend later.)
    raw = get_object_bytes(s3_key)
    if not raw:
        log.warning("kyc_extractor: empty object at %s", s3_key)
        return None

    # MIME normalisation. OpenRouter wants image/jpeg, image/png,
    # image/webp, or image/gif. For PDFs we'd need to convert to image —
    # for now skip PDF extraction (the text-based ingestion path already
    # populates chunks, and the matcher can grade text directly). KYC ID
    # uploads from customer portals are virtually always images.
    if mime == "application/pdf" or s3_key.lower().endswith(".pdf"):
        log.info("kyc_extractor: PDF inputs are deferred to text extractor; skipping vision for %s", s3_key)
        return None
    if mime in ("image/jpg",):
        mime = "image/jpeg"
    if mime not in ("image/jpeg", "image/png", "image/webp", "image/gif"):
        # HEIC etc — we'd need a converter step. Defer.
        log.warning("kyc_extractor: unsupported MIME %s for vision; skipping", mime)
        return None

    # Cap size/dimensions — the OpenRouter cascade stages (Qwen/Claude) 400 on
    # >~5MB images. Keep in sync with classifier + ingestion_vision guards.
    from app.ingestion_vision import prepare_image_for_vision
    raw, mime = prepare_image_for_vision(raw, mime)

    # Cascade: Qwen (preferred, OpenRouter) → Gemini direct → Claude (OR).
    # Each stage falls through to the next on 429 (rate limit), 5xx
    # (transient server error), or non-HTTP exception. 4xx that ISN'T 429
    # (401, 403, 400) is a config problem — raised so it's visible.
    return _extract_with_cascade(raw, mime, doc_type, schema, settings)


def _extract_with_cascade(
    raw: bytes, mime: str, doc_type: str, schema, settings
) -> ExtractionResult | None:
    """Walk the model cascade until one succeeds. Logs each step's model +
    outcome so the reviewer-facing notes / JSON tab reflects which model
    actually ran. OCR augmentation happens INSIDE each _extract_via_*
    function so bboxes are populated regardless of which model wins."""
    preferred = _preferred_model()
    attempts: list[tuple[str, callable]] = []

    # Stage 1 · preferred model via OpenRouter (Qwen by default)
    if preferred and settings.openrouter_api_key:
        attempts.append((
            f"openrouter:{preferred}",
            lambda: _extract_via_openrouter(raw, mime, doc_type, schema, settings, model=preferred),
        ))

    # Stage 2 · Gemini direct
    if settings.google_genai_api_key:
        attempts.append((
            "google:gemini-2.5-flash",
            lambda: _extract_via_gemini(raw, mime, doc_type, schema, settings),
        ))

    # Stage 3 · Claude Haiku via OpenRouter (last-resort fallback)
    if settings.openrouter_api_key and preferred != _ANTHROPIC_VIA_OPENROUTER_MODEL:
        attempts.append((
            f"openrouter:{_ANTHROPIC_VIA_OPENROUTER_MODEL}",
            lambda: _extract_via_openrouter(raw, mime, doc_type, schema, settings, model=_ANTHROPIC_VIA_OPENROUTER_MODEL),
        ))

    if not attempts:
        log.warning("kyc_extractor: no LLM available · skipping extraction")
        return None

    last_exc: Exception | None = None
    for label, fn in attempts:
        try:
            log.info("kyc_extractor: trying %s", label)
            result = fn()
            if result is not None:
                log.info("kyc_extractor: %s succeeded", label)
                return result
            log.info("kyc_extractor: %s returned None — trying next", label)
        except httpx.HTTPStatusError as e:
            status = e.response.status_code
            # Retryable errors → continue down the cascade
            if status in (429, 500, 502, 503, 504):
                log.warning("kyc_extractor: %s · %d %s — trying next", label, status, e.response.reason_phrase or "")
                last_exc = e
                continue
            # 4xx (config / payload) — raise immediately
            log.error("kyc_extractor: %s · %d %s — config issue, NOT retrying", label, status, e.response.reason_phrase or "")
            raise
        except Exception as e:  # noqa: BLE001
            log.warning("kyc_extractor: %s · %s — trying next", label, type(e).__name__)
            last_exc = e
            continue

    # All stages exhausted
    log.error("kyc_extractor: every model in the cascade failed (last_exc=%s)", type(last_exc).__name__ if last_exc else None)
    if last_exc is not None:
        raise last_exc
    return None


def _extract_via_gemini(raw: bytes, mime: str, doc_type: str, schema, settings) -> ExtractionResult | None:
    """Gemini Vision direct · 15 RPM free tier, accurate on Indian/SG/EU IDs.
    Uses Gemini's native responseSchema for strict typed output."""
    import base64
    model = "gemini-2.5-flash"
    # Build a responseSchema from the doc-type schema's fields. Add
    # _doc_confidence as a top-level required key so we get a confidence
    # score back without an extra LLM round-trip.
    properties: dict[str, dict] = {}
    required: list[str] = []
    for fname, fdef in schema.fields.items():
        # Strip 'enum' if present — Gemini's schema only allows enums for
        # string types, and ours already conforms. Map types one-to-one.
        prop = {"type": fdef.get("type", "string")}
        if "description" in fdef:
            prop["description"] = fdef["description"]
        if "enum" in fdef:
            prop["enum"] = fdef["enum"]
        properties[fname] = prop
        required.append(fname)
    properties["_doc_confidence"] = {
        "type": "number",
        "description": "How confident you are the extracted fields are correct (0.0-1.0).",
    }
    properties["_notes"] = {
        "type": "string",
        "description": "Any caveats, ambiguities, or fields you couldn't read clearly.",
    }
    # M40 · per-field 2D bounding boxes. Gemini's standard object-detection
    # format is `[ymin, xmin, ymax, xmax]` normalized to 0-1000. We accept
    # the same shape per extracted field, parse it post-hoc into our
    # standard {x0,y0,x1,y1,page_w,page_h} dict, and stash on the document
    # so the reviewer-facing UI can draw colored field rectangles over the
    # actual image regions.
    properties["_field_bboxes"] = {
        "type": "object",
        "description": (
            "Map of field_name → [ymin, xmin, ymax, xmax] in 0-1000 "
            "normalized coords (Gemini object-detection format). "
            "Each box must tightly enclose that field's printed value on "
            "the image. Omit fields you cannot precisely locate."
        ),
    }
    body = {
        "contents": [{
            "role": "user",
            "parts": [
                {"text": get_prompt("kyc_extraction", schema_label=schema.label, want_bboxes="true")},
                {"inlineData": {"mimeType": mime, "data": base64.b64encode(raw).decode()}},
            ],
        }],
        "generationConfig": {
            "temperature": 0.0,
            "maxOutputTokens": 2048,
            "responseMimeType": "application/json",
            "responseSchema": {
                "type": "object",
                "properties": properties,
                "required": required + ["_doc_confidence"],
            },
        },
    }
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={settings.google_genai_api_key}"
    log.info("kyc_extractor (Gemini): dispatching %d bytes · doc_type=%s", len(raw), doc_type)
    resp = httpx.post(url, json=body, timeout=90.0)
    resp.raise_for_status()
    payload = resp.json()
    try:
        text = "".join(p.get("text", "") for p in (payload["candidates"][0]["content"]["parts"]))
        args = json.loads(text)
    except (KeyError, json.JSONDecodeError, IndexError) as e:
        log.exception("kyc_extractor (Gemini): failed to parse: %s · text=%r", e, payload)
        return None
    confidence = float(args.pop("_doc_confidence", 0.0))
    notes = args.pop("_notes", "")
    # M40 · pull box_2d entries out of the response and convert each from
    # Gemini's [ymin, xmin, ymax, xmax] in 0-1000 space into our standard
    # {x0, y0, x1, y1, page_w, page_h} shape. We keep page_w / page_h =
    # 1000 because the coords already are in that space — the frontend
    # divides by page_w / page_h when projecting back to pixel space.
    raw_bboxes = args.pop("_field_bboxes", None) or {}
    field_bboxes = _convert_gemini_field_bboxes(raw_bboxes)
    # M40 Phase F · deterministic OCR fallback. Even when Gemini returns
    # bboxes, OCR fills gaps; when Gemini returns none, OCR is the entire
    # source. Result: bboxes show up reliably regardless of LLM cooperation.
    field_bboxes = _augment_with_ocr_bboxes(raw, args, field_bboxes)
    return ExtractionResult(
        doc_type=doc_type,
        fields=args,
        confidence=confidence,
        notes=notes,
        model=f"google/{model}",
        raw_response=payload,
        field_bboxes=field_bboxes,
    )


def _augment_with_ocr_bboxes(
    raw_bytes: bytes,
    fields: dict[str, Any],
    llm_bboxes: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Deterministic field-bbox fallback via Tesseract OCR.

    The LLM (Gemini box_2d / Claude best-effort) often returns empty or
    sparse bboxes on real-world IDs. Tesseract is slow but reliable: it
    produces word-level rects on the image, then text-search locates each
    extracted field value and emits a precise bbox.

    Merge policy: LLM bboxes win if present (they're typically tighter
    when accurate); OCR fills in everything the LLM missed. Net effect:
    we get bbox coverage even when the LLM ignores the bbox instruction
    entirely (which is what happened on the user's DL test).

    Best-effort — any OCR failure (Tesseract not installed, parse error,
    image unreadable) leaves llm_bboxes unchanged. The legend on the FE
    still works without bboxes — just no per-field rects on the image.
    """
    if not fields:
        return llm_bboxes
    try:
        # Router picks the best available engine per DOCAIQ_OCR_ENGINE env
        # (default auto = RapidOCR → Tesseract). The locator + bbox-union
        # logic stays in app/agents/ocr.py since the OcrWord shape is the
        # contract every engine returns.
        from app.agents import ocr as ocr_mod
        from app.agents import ocr_router
        words, w, h = ocr_router.extract_words(raw_bytes)
        if not words:
            return llm_bboxes
        ocr_bboxes = ocr_mod.locate_fields(words, fields, w, h)
        # LLM bboxes take precedence; OCR fills the gaps.
        merged = dict(ocr_bboxes)
        merged.update(llm_bboxes)
        return merged
    except Exception as e:  # noqa: BLE001
        log.warning("kyc_extractor: OCR augmentation failed: %s", e)
        return llm_bboxes


def _convert_gemini_field_bboxes(raw: dict) -> dict[str, dict[str, Any]]:
    """Translate Gemini's per-field [ymin, xmin, ymax, xmax] entries (0-1000
    normalized) into our internal {x0, y0, x1, y1, page_w, page_h} shape.

    Drops malformed entries silently — best-effort, the legend still works
    even when some boxes are absent.
    """
    out: dict[str, dict[str, Any]] = {}
    if not isinstance(raw, dict):
        return out
    for fname, box in raw.items():
        if not isinstance(box, (list, tuple)) or len(box) != 4:
            continue
        try:
            ymin, xmin, ymax, xmax = (float(v) for v in box)
        except (TypeError, ValueError):
            continue
        # Reject degenerate / inverted boxes — would render as zero-size or
        # negative on the FE.
        if xmax <= xmin or ymax <= ymin:
            continue
        out[fname] = {
            "x0": xmin, "y0": ymin, "x1": xmax, "y1": ymax,
            "page_w": 1000, "page_h": 1000, "page": 1,
        }
    return out


def _extract_via_openrouter(
    raw: bytes, mime: str, doc_type: str, schema, settings, *, model: str | None = None,
) -> ExtractionResult | None:
    """OpenRouter vision path · works with any vision-capable model.

    `model` defaults to anthropic/claude-haiku-4.5 for back-compat, but the
    cascade dispatcher passes the preferred model (e.g. qwen/qwen2.5-vl-72b-
    instruct) so the user's 1M-token Qwen budget gets used first.

    M40 · also asks for `_field_bboxes` so the FE field overlay works
    regardless of provider. Model-side bbox accuracy varies; OCR
    augmentation (RapidOCR → Tesseract) fills the gaps before this
    function returns.
    """
    chosen_model = model or _ANTHROPIC_VIA_OPENROUTER_MODEL
    body = {
        "model": chosen_model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": get_prompt("kyc_extraction", schema_label=schema.label, want_bboxes="true")},
                    {"type": "image_url", "image_url": {"url": _image_data_url(raw, mime)}},
                ],
            }
        ],
        "tools": [{"type": "function", "function": schema.to_anthropic_tool()}],
        "tool_choice": {"type": "function", "function": {"name": "record_kyc_fields"}},
        # Bumped from 1024 to support the bbox map + notes on top of the
        # field set without truncating mid-emit.
        "max_tokens": 2048,
    }
    headers = {
        "Authorization": f"Bearer {settings.openrouter_api_key}",
        "HTTP-Referer": settings.openrouter_referer,
        "X-Title": settings.openrouter_app_title,
        "Content-Type": "application/json",
    }
    log.info("kyc_extractor (OpenRouter:%s): dispatching %d bytes · doc_type=%s", chosen_model, len(raw), doc_type)
    resp = httpx.post(_OPENROUTER_URL, json=body, headers=headers, timeout=60.0)
    resp.raise_for_status()
    payload = resp.json()
    try:
        tool_calls = payload["choices"][0]["message"].get("tool_calls", [])
        if not tool_calls:
            # Some Qwen / open-source vision models don't always return
            # tool_calls — they sometimes wrap the JSON in the message
            # `content` field instead. Salvage that case so we don't
            # cascade unnecessarily.
            content = payload["choices"][0]["message"].get("content") or ""
            args = _salvage_json_from_content(content)
            if args is None:
                log.warning("kyc_extractor (OpenRouter:%s): no tool_calls + no parseable JSON", chosen_model)
                return None
        else:
            args_str = tool_calls[0]["function"]["arguments"]
            args = json.loads(args_str)
    except (KeyError, json.JSONDecodeError, IndexError) as e:
        log.exception("kyc_extractor (OpenRouter:%s): failed to parse tool-call output: %s", chosen_model, e)
        return None
    confidence = float(args.pop("_doc_confidence", 0.0))
    notes = args.pop("_notes", "")
    # M40 · same conversion as the Gemini path. Whichever model ran (Qwen,
    # Claude, …) returns the `[ymin, xmin, ymax, xmax]` 0-1000 shape we
    # documented in the tool schema — best-effort accuracy.
    raw_bboxes = args.pop("_field_bboxes", None) or {}
    field_bboxes = _convert_gemini_field_bboxes(raw_bboxes)
    # M40 Phase F · deterministic OCR fallback. Open-source vision models
    # often skip the bbox schema fields entirely; OCR fills them in via
    # RapidOCR / Tesseract word-search → bbox union.
    field_bboxes = _augment_with_ocr_bboxes(raw, args, field_bboxes)
    return ExtractionResult(
        doc_type=doc_type,
        fields=args,
        confidence=confidence,
        notes=notes,
        model=chosen_model,
        raw_response=payload,
        field_bboxes=field_bboxes,
    )


def _salvage_json_from_content(content: str) -> dict | None:
    """Open-source vision models (some Qwen variants, smaller models)
    sometimes ignore tool_choice and emit the JSON in the message content
    instead of tool_calls. Best-effort extraction:
      1. Strip markdown fences (```json ... ```)
      2. Find the outermost {…} block
      3. Try json.loads then json-repair as a salvage pass

    Returns None when nothing parseable. Caller treats as "no result" and
    falls through to the next model in the cascade.
    """
    if not content:
        return None
    s = content.strip()
    # Strip ```json … ``` fences
    if s.startswith("```"):
        # Drop the first line (``` or ```json) and any trailing ``` line
        lines = s.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        s = "\n".join(lines).strip()
    # Find the outermost { } block
    start = s.find("{")
    end = s.rfind("}")
    if start < 0 or end < 0 or end <= start:
        return None
    candidate = s[start:end + 1]
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass
    # Last resort — json-repair
    try:
        import json_repair  # type: ignore
        return json_repair.loads(candidate)
    except Exception:  # noqa: BLE001
        return None


def result_to_jsonb(result: ExtractionResult) -> dict:
    """Shape the result for the `documents.extracted_fields` JSONB column."""
    out = {
        "doc_type": result.doc_type,
        "fields": result.fields,
        "confidence": result.confidence,
        "notes": result.notes,
        "model": result.model,
        "extracted_at": datetime.now(timezone.utc).isoformat(),
    }
    # M40 · per-field bboxes from the Gemini Vision path. Mirrors the shape
    # the fact_extractor.py uses for PDFs so the frontend FieldOverlay
    # treats both sources identically.
    if result.field_bboxes:
        out["field_bboxes"] = result.field_bboxes
    return out
