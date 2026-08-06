"""Doc-type classifier — M11.6 classify-first pipeline.

One cheap LLM call per uploaded document, run BEFORE the matcher. Returns
the top-3 doc-type guesses with confidence + a one-line evidence quote.
The targeted matcher then walks only requirements whose `required_docs`
labels overlap the classified type, skipping ~95% of validator calls the
old broad-matcher made (passport vs 'encryption at rest' was always a no).

Vision (image inputs) and text (PDF/document chunk inputs) share the
same downstream prompt + schema; only the request body's content
block differs.
"""

from __future__ import annotations

import base64
import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.config import get_settings
from app.llm import gateway, ledger
from app.model_registry import REGISTRY as _AI_REGISTRY
from app.repositories import documents as docs_repo
from app.storage import get_object_bytes

log = logging.getLogger(__name__)


# The closed enum of doc-types the classifier picks from. Tuned to cover
# the union of every framework pack we ship (SOC 2 / ISO 27001 / HIPAA /
# PCI / NIST / GDPR / KYC) plus financial reconciliation document types.
# Adding a new type is one line here + the router auto-picks it up from
# any requirement's required_docs label.
DOC_TYPES: list[str] = [
    # KYC · identity
    "passport",
    "national_id",
    "driver_licence",
    "utility_bill",
    "bank_statement",
    "credit_card_statement",
    # Income / revenue side — the vendor's books-receivable mirror of
    # expense docs. revenue_invoice = invoice ISSUED by the vendor to a
    # customer. customer_payment = incoming money landing against an
    # invoice (cash, bank transfer, card refund).
    "revenue_invoice",
    "customer_payment",
    "sales_receipt",
    "selfie_or_liveness",
    "tax_document",
    # KYB · business
    "incorporation_certificate",
    "business_profile",                     # ACRA bizfile / UK Companies House / US Sec-of-State entity profile
    "articles_of_association",
    "tax_registration",
    "operating_licence",
    "beneficial_ownership_declaration",
    "shareholder_register",
    "board_resolution",
    "authorized_signatory_list",
    # AML
    "pep_declaration",
    "sanctions_screening_report",
    "adverse_media_report",
    # Financial reconciliation
    "invoice",
    "receipt",
    "expense_claim",
    "revenue_or_payment_notice",
    "payslip",
    "audited_financial_statement",
    # Insurance certificates · all kinds (motor / property / liability /
    # health / life / marine / professional indemnity / D&O / cyber).
    # The fact_extractor's insurance_certificate schema differentiates
    # them via a doc_subtype enum so we don't need separate top-level
    # types here.
    "insurance_certificate",
    "motor_insurance_certificate",
    "cover_note",
    # Compliance / security artefacts
    "policy_or_procedure",                # any internal policy doc
    "audit_report",                       # SOC 2 report, ISO certificate, etc.
    "soc2_report",
    "iso_certificate",
    "training_certificate",               # course / curriculum / education completion · professional certification (issued to a PERSON)
    "pen_test_or_vuln_scan",
    "access_review",
    "security_awareness_training_log",
    "incident_response_record",
    "data_processing_agreement",
    "master_service_agreement",
    "service_level_agreement",
    "sla_or_uptime_evidence",
    "sbom_or_dependency_list",
    "encryption_evidence",
    "backup_or_recovery_evidence",
    "vendor_security_questionnaire",
    "background_check",
    "network_or_architecture_diagram",
    "runbook_or_playbook",
    "code_of_conduct",
    "org_chart",
    "risk_assessment",
    # Health / medical — canonical slugs so lab reports classify consistently (were
    # previously only produced free-form by the LLM → 'lab_report' vs 'medical_lab_report'
    # vs 'laboratory_test_report', which fell out of the Health analytics theme AND the
    # manual Type picker). These align with analytics_themes.HEALTH_TYPES (feedback pk 54).
    "lab_report",
    "medical_report",
    "radiology_report",
    "pathology_report",
    "prescription",
    "discharge_summary",
    "health_checkup",
    "vaccination_record",
    "medical_bill",
    # Misc
    "other",
]

_DOC_TYPES_SET = set(DOC_TYPES)

# Synonym/alias map: free-form doc-type slugs the LLM commonly emits → the canonical
# DOC_TYPES entry they mean. Consulted ONLY when settings.type_canonicalize is on, by
# canonicalize_doc_type() below. All VALUES must be in DOC_TYPES. Extend as new surface
# forms appear in feedback. Keys must NOT already be canonical (those pass through).
_DOC_TYPE_ALIASES: dict[str, str] = {
    # lab / medical family (feedback pk 54 — the reason the canonicalizer exists)
    "medical_lab_report": "lab_report", "laboratory_test_report": "lab_report",
    "laboratory_report": "lab_report", "lab_test": "lab_report",
    "lab_test_report": "lab_report", "lab_result": "lab_report",
    "lab_results": "lab_report", "medical_lab_result": "lab_report",
    "medical_test_report": "lab_report", "blood_test": "lab_report",
    "blood_test_report": "lab_report", "blood_report": "lab_report",
    "test_report": "lab_report", "diagnostic_report": "lab_report",
    "pathology_result": "pathology_report", "radiology_result": "radiology_report",
    "scan_report": "radiology_report", "xray_report": "radiology_report",
    "medical_record": "medical_report", "medical_records": "medical_report",
    "doctor_note": "medical_report", "doctor_letter": "medical_report",
    "medical_certificate": "medical_report", "consultation_report": "medical_report",
    "rx": "prescription", "medication_list": "prescription",
    "discharge_note": "discharge_summary", "immunization_record": "vaccination_record",
    "vaccine_certificate": "vaccination_record", "vaccination_certificate": "vaccination_record",
    "medical_invoice": "medical_bill", "hospital_bill": "medical_bill",
    # finance family
    "bank_account_statement": "bank_statement", "account_statement": "bank_statement",
    "savings_statement": "bank_statement", "cc_statement": "credit_card_statement",
    "creditcard_statement": "credit_card_statement", "card_statement": "credit_card_statement",
    "credit_card_bill": "credit_card_statement", "sales_invoice": "revenue_invoice",
    "tax_invoice": "invoice", "purchase_invoice": "invoice", "vendor_invoice": "invoice",
    "bill": "invoice", "payment_receipt": "receipt", "salary_slip": "payslip",
    "pay_slip": "payslip", "pay_stub": "payslip", "paystub": "payslip",
    "salary_statement": "payslip",
    # identity / utility family
    "id_card": "national_id", "identity_card": "national_id", "nric": "national_id",
    "drivers_license": "driver_licence", "driving_licence": "driver_licence",
    "driving_license": "driver_licence", "drivers_licence": "driver_licence",
    "electricity_bill": "utility_bill", "water_bill": "utility_bill",
    "utility_invoice": "utility_bill",
}


def canonicalize_doc_type(slug: str | None) -> str | None:
    """Map a free-form doc-type slug to a canonical DOC_TYPES entry when it is a known
    synonym; return None when there's no confident mapping (so genuinely-new types stay
    open-vocabulary and the caller keeps the original slug). Callers gate on
    settings.type_canonicalize — this function itself is pure + flag-agnostic."""
    s = (slug or "").strip().lower()
    if not s:
        return None
    if s in _DOC_TYPES_SET:
        return s
    return _DOC_TYPE_ALIASES.get(s)


@dataclass
class ClassificationGuess:
    doc_type: str
    confidence: float
    evidence: str  # one-line quote / reasoning


@dataclass
class ClassificationResult:
    top: ClassificationGuess
    alternatives: list[ClassificationGuess] = field(default_factory=list)
    model: str = ""

    def to_jsonb(self) -> dict[str, Any]:
        return {
            "doc_type": self.top.doc_type,
            "confidence": self.top.confidence,
            "evidence": self.top.evidence,
            "alternatives": [
                {"doc_type": a.doc_type, "confidence": a.confidence, "evidence": a.evidence}
                for a in self.alternatives
            ],
            "model": self.model,
        }


# ── LLM gateway · provider-configurable classify model ────────────────────
# Default is OpenRouter Claude Haiku (legacy). Override with DOCAIQ_CLASSIFIER_MODEL
# to point the classifier at another provider when OpenRouter isn't available —
# e.g. "dashscope/qwen-vl-max", "google/gemini-2.5-flash". A bare id (no provider
# prefix) keeps the legacy OpenRouter routing, so prod is byte-identical unless
# the env is set. (Addresses REVIEW_FINDINGS cost item #3.)
# `or` (not getenv default) so a set-but-EMPTY env (common with compose
# `${VAR:-}`) still falls back to the default instead of becoming "".
_MODEL = os.getenv("DOCAIQ_CLASSIFIER_MODEL") or _AI_REGISTRY["classification"].default_model
# Providers the gateway routes by explicit prefix; anything else falls back to
# OpenRouter (so the bare default "anthropic/claude-haiku-4.5" → openrouter/...).
_DIRECT_PREFIXES = ("openrouter/", "dashscope/", "google/")


def _routed_model() -> str:
    return _MODEL if _MODEL.startswith(_DIRECT_PREFIXES) else f"openrouter/{_MODEL}"


def _routed_provider() -> str:
    return _routed_model().split("/", 1)[0]


_URL = "https://openrouter.ai/api/v1/chat/completions"
_MAX_TEXT_CHARS = 4000  # ~1000 tokens; cheap classifier doesn't need more


def _system_prompt() -> str:
    from app.llm.prompts import get_prompt
    types_str = "\n".join(f"  - {t}" for t in DOC_TYPES)
    return get_prompt("classifier", types_str=types_str)


def _user_text_prompt(text_excerpt: str) -> str:
    return (
        "Classify this document. Here are the first chars of its extracted "
        "text:\n\n---\n"
        f"{text_excerpt}\n"
        "---\n\nReturn the JSON now."
    )


def _image_data_url(image_bytes: bytes, mime: str) -> str:
    b64 = base64.b64encode(image_bytes).decode("ascii")
    return f"data:{mime};base64,{b64}"


def _parse_response(payload: dict) -> ClassificationResult | None:
    try:
        text = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError):
        log.warning("classifier: no content in LLM response")
        return None
    # Strip code fences if the model wrapped the JSON
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip("` \n")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        log.warning("classifier: response wasn't valid JSON: %s · raw: %r", e, text[:200])
        return None
    guesses_raw = data.get("guesses") or []
    guesses: list[ClassificationGuess] = []
    for g in guesses_raw:
        dt = g.get("doc_type")
        if dt not in DOC_TYPES:
            # Flag-gated: map a near-miss ('medical_lab_report') to its canonical type
            # ('lab_report') before falling back to 'other'. Off → identical to before.
            canon = canonicalize_doc_type(dt) if get_settings().type_canonicalize else None
            if canon:
                log.info("classifier: canonicalized doc_type %r → %r", dt, canon)
                dt = canon
            else:
                log.info("classifier: unknown doc_type %r returned; coercing to 'other'", dt)
                dt = "other"
        guesses.append(ClassificationGuess(
            doc_type=dt,
            confidence=float(g.get("confidence", 0.0)),
            evidence=str(g.get("evidence", ""))[:240],
        ))
    if not guesses:
        return None
    guesses.sort(key=lambda x: x.confidence, reverse=True)
    return ClassificationResult(
        top=guesses[0],
        alternatives=guesses[1:3],
        model=_MODEL,
    )


def _llm_call(messages: list[dict]) -> tuple[dict | None, dict]:
    """Wrap the OpenRouter call via `gateway.call()` (TODO #41) so the
    classifier now flows through the same provider abstraction as the
    cascade. Returns (payload-or-None, telemetry) — telemetry is always
    returned so the caller writes the ledger row whether the call
    succeeded or failed.

    `messages` is the OpenAI-style list of {role, content} dicts. We
    translate each into a gateway.Message so multi-modal (image_url)
    blocks survive — that's the path classify_document's image branch
    uses for vision OCR.
    """
    settings = get_settings()
    routed = _routed_model()
    provider = _routed_provider()
    telemetry: dict = {
        "model": _MODEL,
        "provider": provider,
        "input_tokens": 0,
        "output_tokens": 0,
        "latency_ms": 0,
        "status": "failed",
        "error": None,
    }
    # Provider-aware key gate: only require the key the routed model actually uses.
    _missing = (
        (provider == "openrouter" and not settings.openrouter_api_key)
        or (provider == "dashscope" and not settings.dashscope_api_key)
        or (provider == "google" and not settings.google_genai_api_key)
    )
    if _missing:
        log.warning("classifier: no API key for provider %s; skipping classification", provider)
        telemetry["error"] = "no_api_key"
        telemetry["provider"] = "stub"
        return None, telemetry

    gw_messages = [
        gateway.Message(role=m["role"], content=m["content"]) for m in messages
    ]
    try:
        # M44.P11 · tenant context → PII redaction + audit on the classifier
        # call (it sees raw document text, which can carry card/bank data).
        from app.db import get_current_tenant as _get_tid
        try:
            _tid = _get_tid()
        except Exception:  # noqa: BLE001
            log.warning("classifier: get_current_tenant() failed — LLM call will "
                        "proceed without PII redaction; check TenantMiddleware")
            _tid = None
        result = gateway.call(
            model=routed,
            messages=gw_messages,
            max_tokens=600,
            temperature=0.2,
            tenant_id=_tid,
            task_kind="classify",
        )
    except Exception as e:  # noqa: BLE001 — provider / network
        log.warning("classifier: LLM call failed: %s", e)
        telemetry["error"] = str(e)[:200]
        return None, telemetry

    telemetry["latency_ms"] = int(result.latency_ms)
    telemetry["input_tokens"] = int(result.input_tokens or 0)
    telemetry["output_tokens"] = int(result.output_tokens or 0)
    telemetry["provider"] = result.provider
    telemetry["status"] = "ok"
    # Rebuild a minimal OpenAI-shape payload so `_parse_response` (which
    # walks payload["choices"][0]["message"]["content"]) keeps working
    # unchanged. Avoids a parallel migration of the parser.
    payload = result.raw_json or {
        "choices": [{"message": {"content": result.text or ""}}],
    }
    return payload, telemetry


# Anthropic Claude Haiku 4.5 pricing (via OpenRouter, 2026-Q1):
#   $1/M input tokens · $5/M output tokens (5× spread)
# Classifier is short-input + tiny JSON output, so input dominates here.
_HAIKU_COST_IN = 1.0
_HAIKU_COST_OUT = 5.0


def classify_text(text_excerpt: str) -> ClassificationResult | None:
    """Classify a text-extractable document (clean PDF, text upload).
    Cheap path — no vision, just a small Haiku call on the first ~4KB."""
    excerpt = (text_excerpt or "")[:_MAX_TEXT_CHARS]
    if not excerpt.strip():
        return None
    messages = [
        {"role": "system", "content": _system_prompt()},
        {"role": "user", "content": _user_text_prompt(excerpt)},
    ]
    payload, _telemetry = _llm_call(messages)
    if payload is None:
        return None
    return _parse_response(payload)


def classify_image(image_bytes: bytes, mime: str) -> tuple[ClassificationResult | None, dict]:
    """Classify an image (or image-PDF rasterised page). Vision call.
    Returns (result, telemetry_dict) so callers can record ledger rows."""
    if mime in ("image/jpg",):
        mime = "image/jpeg"
    if mime not in ("image/jpeg", "image/png", "image/webp", "image/gif"):
        log.info("classifier: unsupported MIME %s; skipping vision classify", mime)
        return None, {}
    # Cap size/dimensions — Anthropic-via-OpenRouter 400s on >~5MB images
    # (a 6MB screenshot otherwise fails classification → no doc_type/category).
    from app.ingestion_vision import prepare_image_for_vision
    image_bytes, mime = prepare_image_for_vision(image_bytes, mime)
    data_url = _image_data_url(image_bytes, mime)
    messages = [
        {"role": "system", "content": _system_prompt()},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Classify this document. Return the JSON now."},
                {"type": "image_url", "image_url": {"url": data_url}},
            ],
        },
    ]
    payload, telemetry = _llm_call(messages)
    if payload is None:
        return None, telemetry
    return _parse_response(payload), telemetry


def _record_ledger(db: Session, telemetry: dict) -> None:
    """Write one LLMCall row from a `_llm_call` telemetry dict. Always
    safe to call — `ledger.record_call` swallows DB errors."""
    ledger.record_call(
        db,
        task="classify",
        tier="t2",  # classifier always uses Haiku-class (a t2 model)
        provider=telemetry["provider"],
        model=telemetry["model"],
        input_tokens=telemetry["input_tokens"],
        output_tokens=telemetry["output_tokens"],
        cost_per_input_mtok=_HAIKU_COST_IN,
        cost_per_output_mtok=_HAIKU_COST_OUT,
        latency_ms=telemetry["latency_ms"],
        status=telemetry["status"],
        error=telemetry["error"],
    )


def classify_document(db: Session, document_pk: int) -> ClassificationResult | None:
    """Resolve doc → bytes / text and dispatch to the right classifier.

    Every code path records exactly one LLMCall row (or zero if there
    were no bytes/text to send) so the Spend dashboard reflects
    classifier usage accurately."""
    doc = docs_repo.get_row_by_pk(db, document_pk)
    if doc is None:
        return None

    # Image branch — delegate to classify_image() so the vision logic
    # lives in one place. Ledger telemetry is recorded here after the call.
    mime = (doc.mime_type or "").lower()
    if mime.startswith("image/") and doc.s3_key:
        raw = get_object_bytes(doc.s3_key)
        if not raw:
            return None
        result, telemetry = classify_image(raw, mime)
        if telemetry:
            _record_ledger(db, telemetry)
        return result

    # Text branch — use already-extracted chunks (cheap, no re-OCR)
    chunks = docs_repo.chunks_for_doc(db, doc.pk, limit=4)
    text = "\n".join((c.text or "") for c in chunks)
    if text.strip():
        excerpt = text[:_MAX_TEXT_CHARS]
        messages = [
            {"role": "system", "content": _system_prompt()},
            {"role": "user", "content": _user_text_prompt(excerpt)},
        ]
        payload, telemetry = _llm_call(messages)
        if telemetry:
            _record_ledger(db, telemetry)
        if payload is None:
            return None
        return _parse_response(payload)

    # Nothing to work with (image-only PDF without OCR yet, or empty doc)
    log.info("classifier: doc pk=%s has no text or image to classify", document_pk)
    return None


def persist(db: Session, document_pk: int, result: ClassificationResult) -> None:
    """Write the classification onto the document row + commit."""
    doc = docs_repo.get_row_by_pk(db, document_pk)
    if doc is None:
        return
    blob = result.to_jsonb()
    doc.doc_type = blob["doc_type"]
    doc.doc_type_confidence = blob["confidence"]
    doc.doc_type_alternatives = blob["alternatives"]
    db.commit()
    log.info(
        "classifier: doc pk=%s → %s (conf=%.2f); alts=%s",
        document_pk, blob["doc_type"], blob["confidence"],
        [a["doc_type"] for a in blob["alternatives"]],
    )
