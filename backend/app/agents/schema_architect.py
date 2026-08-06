"""Schema-Architect — a strong LLM drafts a rich extraction schema for a document type.

Given a type slug (+ optional label / sample document text), returns
{label, domain, description, fields:{...}} — the fields an analyst would want extracted from
EVERY document of that type, including nested arrays for repeated rows (line_items, tests,
authors, transactions, …). Runs ONCE per type (seed batch or on-the-fly for a new type); the
result is `proposed` and must be HITL-approved before it goes live.
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.config import get_settings
from app.llm import gateway
from app.llm.gateway import Message
from app.llm.prompts import get_prompt
from app.model_registry import REGISTRY as _AI_REGISTRY

log = logging.getLogger("docaiq.schema_architect")

_DOMAINS = ("identity", "banking", "investments", "ap_ar", "payroll_hr", "legal", "corporate",
            "insurance", "medical", "education", "real_estate", "logistics", "utilities",
            "travel", "technical")

_SYS = (
    "You are a document-schema architect. Given a document TYPE (and optionally a sample), "
    "design the extraction schema an analyst would want: the fields worth pulling from EVERY "
    "document of this type, including NESTED ARRAYS for repeated structures (line items, test "
    "results, authors, transactions, parties, holdings, coverage items, etc.).\n\n"
    "Return STRICT JSON only, this exact shape:\n"
    "{\n"
    '  "label": "<human title>",\n'
    '  "domain": "<one of: ' + ", ".join(_DOMAINS) + '>",\n'
    '  "description": "<one line>",\n'
    '  "rationale": "<2-4 sentences: why THIS field set fits this type, and — important for '
    'unusual/unknown types — call out what you were UNSURE about and any assumptions you made>",\n'
    '  "confidence": <0.0-1.0: how confident you are this schema is right for the type>,\n'
    '  "fields": {\n'
    '     "<snake_case_field>": {"type": "string|number|date|object|array",\n'
    '        "description": "...", "required": true|false,\n'
    '        "items": {"type":"object","properties":{...}},   // for arrays of rows\n'
    '        "properties": {...}                               // for object fields\n'
    "     }\n"
    "  }\n"
    "}\n\n"
    "Rules: 8-20 top-level fields. Use arrays with `items.properties` for repeated rows. Prefer "
    "specific high-value fields over generic ones. snake_case names. Mark the 2-5 truly essential "
    "fields required. For identity documents (passport / national ID / driver licence), keep "
    "nationality, race/ethnicity, and place/country of birth as SEPARATE fields, and in the "
    "nationality field's description note that it is citizenship — NOT race and NOT birthplace "
    "(some cards, e.g. Singapore NRIC, print Race + Country of Birth but no nationality). "
    "Every key under `fields` MUST be a real field name whose value is the "
    "definition OBJECT above — never place a definition's own metadata (\"type\", \"required\", "
    "\"description\", \"properties\", \"items\") as a sibling key of `fields`. Output ONLY the JSON, "
    "no prose."
)

# JSON-Schema definition-metadata words. The model occasionally flattens a field-definition's own
# metadata up into the top-level `fields` map (a sibling "required"/"description"/"type" carrying a
# bare value instead of a nested field), which then renders as a permanent phantom 'missing' row
# downstream. Mirror of app.services.schema_json._RESERVED_META (render-time defence #286).
_META_KEYS = {"required", "description", "type", "properties", "enum", "format",
              "items", "title", "examples", "default"}
_FIELD_TYPES = {"string", "number", "date", "object", "array", "boolean", "integer"}


def _sanitize_fields(fields: dict) -> dict:
    """Normalize the LLM's `fields` map so every entry is a well-formed field definition:
    drop leaked definition-metadata keys (a reserved word carrying a NON-dict value is a leak,
    not a field) and coerce any bare/shorthand definition into a minimal valid object. A genuine
    field literally named 'type' etc. carries a dict definition and is kept."""
    clean: dict = {}
    for key, defn in fields.items():
        if not isinstance(key, str) or not key.strip():
            continue
        if key in _META_KEYS and not isinstance(defn, dict):
            continue
        if isinstance(defn, dict):
            d = dict(defn)
            t = str(d.get("type") or "string").strip().lower()
            d["type"] = t if t in _FIELD_TYPES else "string"
            d["required"] = bool(d.get("required"))
            clean[key] = d
        else:
            # bare shorthand ("field": "string" | null) → minimal valid definition
            t = str(defn or "string").strip().lower()
            clean[key] = {"type": t if t in _FIELD_TYPES else "string", "required": False}
    return clean


def draft_schema(db: Session, *, type_slug: str, label: str | None = None,
                 sample_text: str | None = None, model: str | None = None) -> dict:
    """Draft a rich schema for `type_slug`. Raises ValueError if the model returns nothing usable."""
    s = get_settings()
    mdl = model or getattr(s, "strong_extract_model", None) or _AI_REGISTRY["strong_extraction"].default_model
    # The gateway routes by a provider prefix; a bare model (e.g. "qwen-max") falls to the stub.
    # qwen-* live on DashScope; prefix it if the caller didn't specify a provider.
    if "/" not in mdl:
        mdl = f"dashscope/{mdl}"

    user = f"TYPE: {type_slug}"
    if label:
        user += f"  (a.k.a. {label})"
    if sample_text:
        user += f"\n\nSAMPLE DOCUMENT (first 3000 chars):\n{sample_text[:3000]}"
    user += "\n\nDesign the schema now. JSON only."

    result = gateway.call(
        mdl,
        [Message(role="system", content=get_prompt("schema_architect")), Message(role="user", content=user)],
        temperature=0.2, max_tokens=2000, structured=True, task_kind="schema_architect",
    )
    data = result.structured if isinstance(result.structured, dict) else None
    if not data or not isinstance(data.get("fields"), dict) or not data["fields"]:
        raise ValueError(f"Schema-Architect returned no usable schema for '{type_slug}'")

    fields = _sanitize_fields(data["fields"])
    if not fields:
        raise ValueError(f"Schema-Architect returned no usable schema for '{type_slug}'")

    data.setdefault("label", label or type_slug.replace("_", " ").title())
    dom = str(data.get("domain") or "").strip().lower()
    data["domain"] = dom if dom in _DOMAINS else ""
    data.setdefault("description", "")
    try:
        conf = float(data.get("confidence"))
    except (TypeError, ValueError):
        conf = None
    return {"label": data["label"], "domain": data["domain"],
            "description": data["description"], "fields": fields,
            "rationale": str(data.get("rationale") or "").strip(),
            "confidence": conf, "model": mdl}


# The v1 seed taxonomy (~123 types across 15 domains).
# VALIDATION_BATCH is the first 10 (already reviewed); the full set is generated for HITL review.
VALIDATION_BATCH: list[tuple[str, str]] = [
    ("passport", "Passport"), ("national_id", "National ID card"),
    ("driver_license", "Driver's license"), ("invoice", "Invoice"), ("receipt", "Receipt"),
    ("bank_statement", "Bank statement"), ("lab_report", "Medical lab report"),
    ("research_paper", "Research paper"), ("event_ticket", "Event ticket"),
    ("bill_of_lading", "Bill of lading"),
]

SEED_TAXONOMY: list[tuple[str, str]] = VALIDATION_BATCH + [
    # identity & travel
    ("visa", "Visa"), ("residence_permit", "Residence permit"),
    ("birth_certificate", "Birth certificate"), ("marriage_certificate", "Marriage certificate"),
    ("social_security_card", "Social security card"), ("voter_id", "Voter ID"),
    ("work_permit", "Work permit"),
    # banking & payments
    ("credit_card_statement", "Credit card statement"), ("cheque", "Cheque"),
    ("remittance_advice", "Remittance advice"),
    ("wire_transfer_confirmation", "Wire transfer confirmation"),
    ("loan_agreement", "Loan agreement"), ("mortgage_statement", "Mortgage statement"),
    ("standing_instruction", "Standing instruction"), ("iban_letter", "IBAN letter"),
    # investments & tax
    ("brokerage_statement", "Brokerage statement"), ("investment_statement", "Investment statement"),
    ("crypto_transaction", "Crypto transaction"), ("tax_return", "Tax return"),
    ("w2", "W-2"), ("form_1099", "Form 1099"), ("p60", "P60"), ("form_16", "Form 16"),
    ("capital_gains_statement", "Capital gains statement"),
    # AP / AR / commercial
    ("revenue_invoice", "Revenue invoice"), ("purchase_order", "Purchase order"),
    ("quotation", "Quotation"), ("proforma_invoice", "Proforma invoice"),
    ("delivery_note", "Delivery note"), ("packing_slip", "Packing slip"),
    ("credit_note", "Credit note"), ("debit_note", "Debit note"),
    ("statement_of_account", "Statement of account"),
    # payroll & HR
    ("payslip", "Payslip"), ("offer_letter", "Offer letter"),
    ("employment_contract", "Employment contract"), ("timesheet", "Timesheet"),
    ("i9_form", "I-9 form"), ("performance_review", "Performance review"),
    ("benefits_enrollment", "Benefits enrollment"),
    ("employment_verification", "Employment verification"),
    # legal & contracts
    ("contract", "Contract"), ("nda", "NDA"), ("lease_agreement", "Lease agreement"),
    ("terms_of_service", "Terms of service"), ("power_of_attorney", "Power of attorney"),
    ("will", "Will"), ("deed_of_conveyance", "Deed of conveyance"), ("affidavit", "Affidavit"),
    ("court_filing", "Court filing"), ("mou", "Memorandum of understanding"),
    # corporate & compliance
    ("business_registration", "Business registration"),
    ("certificate_of_incorporation", "Certificate of incorporation"),
    ("articles_of_association", "Articles of association"),
    ("board_resolution", "Board resolution"), ("shareholder_agreement", "Shareholder agreement"),
    ("annual_report", "Annual report"), ("audit_report", "Audit report"),
    ("beneficial_ownership", "Beneficial ownership declaration"), ("aml_report", "AML report"),
    # insurance
    ("insurance_policy", "Insurance policy"), ("insurance_claim", "Insurance claim"),
    ("insurance_certificate", "Insurance certificate"),
    ("explanation_of_benefits", "Explanation of benefits"),
    ("actuarial_report", "Actuarial report"), ("cover_note", "Cover note"),
    # medical & health
    ("medical_report", "Medical report"), ("prescription", "Prescription"),
    ("discharge_summary", "Discharge summary"), ("radiology_report", "Radiology report"),
    ("vaccination_record", "Vaccination record"), ("medical_bill", "Medical bill"),
    ("referral_letter", "Referral letter"), ("patient_intake", "Patient intake form"),
    ("clinical_trial_document", "Clinical trial document"),
    # education & research
    ("thesis", "Thesis"), ("academic_transcript", "Academic transcript"),
    ("degree_certificate", "Degree certificate"), ("syllabus", "Syllabus"),
    ("grant_proposal", "Grant proposal"), ("patent", "Patent"),
    ("conference_paper", "Conference paper"),
    # real estate
    ("property_deed", "Property deed"), ("title_report", "Title report"),
    ("rental_agreement", "Rental agreement"), ("property_tax_bill", "Property tax bill"),
    ("home_inspection_report", "Home inspection report"), ("appraisal_report", "Appraisal report"),
    ("hoa_document", "HOA document"),
    # logistics & trade
    ("air_waybill", "Air waybill"), ("commercial_invoice", "Commercial invoice"),
    ("packing_list", "Packing list"), ("customs_declaration", "Customs declaration"),
    ("certificate_of_origin", "Certificate of origin"), ("shipping_manifest", "Shipping manifest"),
    ("letter_of_credit", "Letter of credit"), ("import_license", "Import license"),
    # utilities & telecom
    ("utility_bill", "Utility bill"), ("phone_bill", "Phone bill"),
    ("internet_bill", "Internet bill"), ("subscription_invoice", "Subscription invoice"),
    # travel & hospitality
    ("boarding_pass", "Boarding pass"), ("hotel_reservation", "Hotel reservation"),
    ("itinerary", "Itinerary"), ("travel_insurance", "Travel insurance"),
    ("car_rental_agreement", "Car rental agreement"),
    # technical & engineering
    ("datasheet", "Datasheet"), ("technical_specification", "Technical specification"),
    ("bill_of_materials", "Bill of materials"), ("safety_data_sheet", "Safety data sheet"),
    ("test_report", "Test report"), ("calibration_certificate", "Calibration certificate"),
    ("engineering_drawing", "Engineering drawing"),
]
