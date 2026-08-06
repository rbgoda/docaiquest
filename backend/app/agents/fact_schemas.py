"""Built-in extraction schemas (data) — split out of fact_extractor.py.

FactSchema + DOC_TYPE_TO_SCHEMA + the item templates + the SCHEMAS dict. Pure data; the
extraction LOGIC stays in fact_extractor.py, which imports these.
"""
from __future__ import annotations

from dataclasses import dataclass, field  # noqa: F401 — field kept for schema authoring
from typing import Any


DOC_TYPE_TO_SCHEMA: dict[str, str] = {
    "master_service_agreement": "agreement",
    "service_level_agreement": "agreement",
    "data_processing_agreement": "agreement",
    "invoice": "invoice",
    "receipt": "receipt",
    "expense_claim": "receipt",
    "bank_statement": "bank_statement",
    "audited_financial_statement": "bank_statement",
    "credit_card_statement": "bank_statement",
    "policy_or_procedure": "policy_or_procedure",
    "code_of_conduct": "policy_or_procedure",
    "runbook_or_playbook": "policy_or_procedure",
    # Insurance certificates · motor / property / liability / health / life /
    # marine / professional indemnity / D&O / cyber. All share a common
    # field set (insurer, policy_number, insured_party, coverage type,
    # effective/expiry, premium, sum insured, exclusions) so one schema
    # covers them.
    "insurance_certificate": "insurance_certificate",
    "motor_insurance_certificate": "insurance_certificate",
    "cover_note": "insurance_certificate",
    "iso_certificate": "certificate",
    "training_certificate": "certificate",
    "soc2_report": "certificate",
    "incorporation_certificate": "certificate",
    "audit_report": "certificate",
    # ACRA bizfile / Companies House / Sec-of-State / MCA — has its own
    # schema because the field set is much wider than a certificate
    # (officers, status, business activities, share capital, addresses).
    "business_profile": "business_profile",
    # Income / revenue side
    "revenue_invoice": "revenue_invoice",
    "sales_receipt": "revenue_invoice",  # functionally similar — buyer's receipt mirrors seller's invoice
    "customer_payment": "customer_payment",
    # ID documents — text-based extraction from already-OCR'd chunks.
    # (The vision-based kyc_extractor still runs on the raw image when the
    # matcher attaches the doc to a KYC-* requirement; this path gives
    # reviewers a Key Facts card immediately on open.)
    "passport": "id_document",
    "national_id": "id_document",
    "driver_licence": "id_document",
    # Résumé / CV — the free-form slug the (open-vocabulary) classifier emits for a
    # candidate profile. Universal drops the education rows (SSC/HSC/degrees) into a
    # single key_fact, so this doc type gets its own curated schema with an education[]
    # array. Allow-listed through the documents-product universal-force in fact_extractor.
    "resume": "resume",
    "cv": "resume",
    "curriculum_vitae": "resume",
}


# ── Schema definitions ────────────────────────────────────────────────────

@dataclass(frozen=True)
class FactSchema:
    label: str
    description: str            # tells the LLM what kind of doc to expect
    fields: dict[str, dict[str, Any]]

    def to_openrouter_tool(self) -> dict[str, Any]:
        """OpenAI-compatible tool function shape (OpenRouter relays to the
        underlying provider — Anthropic, OpenAI, etc — and they all accept
        this format). Adds two meta-fields: _doc_confidence and _notes."""
        return {
            "type": "function",
            "function": {
                "name": "record_doc_facts",
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        **self.fields,
                        "_doc_confidence": {
                            "type": "number",
                            "description": "Your overall confidence (0.0-1.0) that this document matches the expected schema and the fields are accurate. ≥ 0.85 means high-quality extraction.",
                        },
                        "_notes": {
                            "type": "string",
                            "description": "Brief notes — anything unusual, ambiguous, or worth flagging. Empty if nothing.",
                        },
                    },
                    "required": ["_doc_confidence"],
                },
            },
        }


# Convenience builders for fields that recur across schemas. Inline rather
# than inherited because each schema is meant to be a self-contained contract
# the LLM reads in one place.
_PARTY_ITEM = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "Legal name as written in the document."},
        "role": {"type": "string", "description": "Role label as used in the document — Contractor, Customer, Provider, LHUB, Party A, etc."},
    },
    "required": ["name"],
}

_SIGNATURE_ITEM = {
    "type": "object",
    "properties": {
        "signatory_name": {"type": "string"},
        "signatory_role": {"type": "string", "description": "Their role / party (Contractor, Authorised Signatory, CEO, etc)."},
        "signature_date": {"type": "string", "description": "YYYY-MM-DD; empty string if no date visible."},
        "page": {"type": "integer", "description": "Page number where the signature appears."},
        "evidence_quote": {"type": "string", "description": "Short quote from the document supporting this signature block."},
    },
    "required": ["signatory_name"],
}

_LINE_ITEM = {
    "type": "object",
    "properties": {
        "description": {"type": "string"},
        "sku": {"type": "string", "description": "Item / SKU / product code if printed, else empty."},
        "quantity": {"type": "string", "description": "As printed — keep units (hrs, units, days)."},
        "unit_price": {"type": "string"},
        "amount": {"type": "string", "description": "Line total (qty × unit_price)."},
    },
    "required": ["description"],
}

_TXN_ITEM = {
    "type": "object",
    "properties": {
        "date": {"type": "string", "description": "YYYY-MM-DD"},
        "posted_date": {"type": "string", "description": "Post date if different from txn date (CC statements often show both). YYYY-MM-DD."},
        "description": {"type": "string", "description": "Merchant or full line text as printed. This is what we match against receipt vendor_name during reconciliation."},
        "merchant": {"type": "string", "description": "Just the merchant / vendor name extracted from the description, if separable (e.g. line 'KFC RESTAURANT #C160014 IL' → merchant='KFC')."},
        "amount": {"type": "string", "description": "Amount as printed, with currency symbol if shown."},
        "direction": {"type": "string", "enum": ["credit", "debit", "unknown"], "description": "debit = charge / money out. credit = payment / refund / money in."},
        "category": {"type": "string", "description": "CC issuer's category label if printed (e.g. 'Dining', 'Travel', 'Fuel'). Empty otherwise."},
        "balance_after": {"type": "string", "description": "Running balance after this txn, if shown."},
    },
    "required": ["description", "amount"],
}


SCHEMAS: dict[str, FactSchema] = {
    # ── Agreements ──────────────────────────────────────────────────────
    "agreement": FactSchema(
        label="Service / MSA / DPA / SLA agreement",
        description="Extract the structured facts of a service agreement, master service agreement, data processing agreement, or service-level agreement.",
        fields={
            "agreement_type": {
                "type": "string",
                "description": "One short phrase — 'Master Service Agreement', 'Training Services Agreement', 'Data Processing Agreement', etc.",
            },
            "parties": {"type": "array", "items": _PARTY_ITEM, "description": "Every named party plus their role label."},
            "effective_date": {"type": "string", "description": "YYYY-MM-DD. Empty if unspecified."},
            "expiry_date": {"type": "string", "description": "YYYY-MM-DD. Empty if the agreement is open-ended."},
            "term_description": {"type": "string", "description": "How the term is described in the doc (e.g. '12 months from effective date', 'until terminated by either party with 30 days notice')."},
            "jurisdiction": {"type": "string", "description": "Governing law / jurisdiction (e.g. 'Singapore', 'Delaware')."},
            "signature_blocks": {
                "type": "array",
                "items": _SIGNATURE_ITEM,
                "description": "Every signature block found. Include unsigned blocks (where a name appears but no date) — set signature_date to empty string in that case. If no signature pages exist at all, return an empty array.",
            },
            "is_signed": {"type": "boolean", "description": "True iff at least one signature block has both a signatory_name AND a non-empty signature_date."},
            "key_obligations": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Up to 6 one-line bullets capturing the most material obligations of either party.",
            },
            "termination_clause_summary": {"type": "string", "description": "One-sentence summary of how the agreement ends. Empty if not addressed."},
            "total_value": {"type": "string", "description": "Total contract value if explicitly stated (with currency). Empty if not stated."},
        },
    ),

    # ── Invoices ────────────────────────────────────────────────────────
    "invoice": FactSchema(
        label="Invoice",
        description="Extract the structured facts of a sales/services invoice.",
        fields={
            "invoice_number": {"type": "string"},
            "issue_date": {"type": "string", "description": "YYYY-MM-DD"},
            "due_date": {"type": "string", "description": "YYYY-MM-DD; empty if not stated."},
            "vendor": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "address": {"type": "string"},
                    "tax_id": {"type": "string"},
                },
            },
            "customer": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "address": {"type": "string"},
                },
            },
            "line_items": {"type": "array", "items": _LINE_ITEM, "description": "Each line on the invoice (description, sku, quantity, unit_price, amount). Cap at 30 — if more, return the largest 30 by amount."},
            "subtotal": {"type": "string", "description": "Sum of line items before tax/discount."},
            "discount": {"type": "string", "description": "Total discount amount, if any."},
            "tax": {"type": "string", "description": "Total tax amount as printed."},
            "total": {"type": "string", "description": "Grand total (subtotal − discount + tax)."},
            "currency": {"type": "string", "description": "ISO 4217 code (USD, GBP, SGD, INR, ...)."},
            "payment_terms": {"type": "string", "description": "Net 30, on receipt, etc."},
        },
    ),

    # ── Revenue invoices (the vendor ISSUED to a customer) ───────────────
    # Mirror of the `invoice` schema but with the audited entity as seller
    # rather than buyer. Drives income roll-up + reconciliation against
    # bank-statement CREDIT transactions (incoming money) instead of debits.
    "revenue_invoice": FactSchema(
        label="Revenue invoice / sales invoice / tax invoice issued to a customer",
        description=(
            "Extract structured facts from an invoice ISSUED by the audited "
            "entity (the seller / service provider) to a customer or client. "
            "Distinguish from an expense invoice by who appears as the issuer."
        ),
        fields={
            "invoice_number": {"type": "string"},
            "issue_date": {"type": "string", "description": "YYYY-MM-DD"},
            "due_date": {"type": "string", "description": "YYYY-MM-DD"},
            "seller": {
                "type": "object",
                "description": "The audited entity — the party issuing this invoice.",
                "properties": {
                    "name": {"type": "string"},
                    "address": {"type": "string"},
                    "tax_id": {"type": "string", "description": "GST/VAT/EIN/UEN/TIN as printed."},
                },
            },
            "customer": {
                "type": "object",
                "description": "The party being billed.",
                "properties": {
                    "name": {"type": "string"},
                    "address": {"type": "string"},
                    "customer_id": {"type": "string"},
                },
            },
            "line_items": {
                "type": "array",
                "items": _LINE_ITEM,
                "description": "Every billable line. Cap at 50 — keep all detail; downstream needs each line categorised.",
            },
            "subtotal": {"type": "string"},
            "discount": {"type": "string", "description": "Total discount amount, if any."},
            "tax": {"type": "string", "description": "GST/VAT amount."},
            "tax_rate": {"type": "string", "description": "GST/VAT rate as printed (e.g. '7%', '9%', '20%')."},
            "total": {"type": "string", "description": "Grand total — the amount the customer owes."},
            "currency": {"type": "string", "description": "ISO 4217."},
            "payment_terms": {"type": "string", "description": "'Net 30', 'On receipt', 'Due upon completion', etc."},
            "status": {
                "type": "string",
                "enum": ["draft", "issued", "partially_paid", "paid", "overdue", "cancelled", "unknown"],
                "description": "Status as printed on the doc or inferred from any 'PAID' stamp.",
            },
            "revenue_category": {
                "type": "string",
                "description": (
                    "What KIND of revenue this is. Pick one of: Sales, "
                    "Service Revenue, Consulting, Subscription Revenue, "
                    "Rental, Interest, Dividends, Tax Refund, Other Income."
                ),
            },
        },
    ),

    # ── Customer payments (incoming money) ───────────────────────────────
    # Mirrors `receipt` but for incoming cash. A customer payment is what
    # arrives on the seller's side: bank transfer notification, card
    # settlement, cheque, cash receipt. Pairs with a revenue_invoice via
    # invoice number when present.
    "customer_payment": FactSchema(
        label="Customer payment / payment received / settlement notice",
        description=(
            "Extract structured facts from a payment notification, payment "
            "advice, settlement notice, or receipt of incoming money. The "
            "audited entity is the recipient."
        ),
        fields={
            "payment_reference": {"type": "string", "description": "Payment confirmation / settlement reference number."},
            "payment_date": {"type": "string", "description": "YYYY-MM-DD"},
            "amount": {"type": "string"},
            "currency": {"type": "string", "description": "ISO 4217."},
            "payer_name": {"type": "string", "description": "Customer or counterparty who paid."},
            "payer_account": {"type": "string", "description": "Last 4 of payer's account / card if shown."},
            "recipient_account": {"type": "string", "description": "Last 4 of the audited entity's receiving account."},
            "method": {
                "type": "string",
                "enum": ["bank_transfer", "card", "cheque", "cash", "PayNow", "FAST", "wire", "ACH", "other"],
            },
            "against_invoice_number": {"type": "string", "description": "Invoice this payment settles, if referenced. Used by the reconciler to match invoice ↔ payment."},
            "memo": {"type": "string", "description": "Any reference / memo line / payment note."},
            "revenue_category": {
                "type": "string",
                "description": "Same enum as revenue_invoice.revenue_category — Sales / Service Revenue / Consulting / Subscription Revenue / Rental / Interest / Dividends / Tax Refund / Other Income.",
            },
        },
    ),

    # ── Receipts / expense claims ───────────────────────────────────────
    "receipt": FactSchema(
        label="Receipt or expense claim",
        description="Extract the structured facts of a payment receipt or filed expense claim.",
        fields={
            "receipt_number": {"type": "string"},
            "date": {"type": "string", "description": "YYYY-MM-DD"},
            "vendor_name": {"type": "string"},
            "customer_or_claimant": {"type": "string"},
            "items": {"type": "array", "items": _LINE_ITEM},
            "total": {"type": "string"},
            "currency": {"type": "string"},
            "payment_method": {"type": "string", "description": "card, cash, bank transfer, etc."},
            "category": {"type": "string", "description": "If an expense claim — travel, meals, software, etc."},
        },
    ),

    # ── Bank + credit-card statements ──────────────────────────────────
    # Same schema covers both — credit-card-specific fields are optional
    # so they're empty for plain bank statements but populated for
    # statements that look like a credit card bill.
    "bank_statement": FactSchema(
        label="Bank statement / credit card statement / financial statement",
        description=(
            "Extract structured facts from a bank statement, credit-card "
            "statement, or audited financial statement. Capture EVERY line "
            "item the document lists — these feed downstream reconciliation "
            "against receipts."
        ),
        fields={
            "bank_or_org_name": {"type": "string", "description": "Issuer — bank name for bank statements, card issuer for CC (e.g. 'Chase', 'HDFC', 'DBS')."},
            "statement_kind": {
                "type": "string",
                "enum": ["bank_statement", "credit_card_statement", "audited_financial_statement", "other"],
                "description": "Which subtype this document is. Helps downstream tooling pick the right vocabulary.",
            },
            "account_holder": {"type": "string", "description": "Account holder / cardholder name as printed."},
            "account_number_last_4": {"type": "string", "description": "Last 4 digits of the account or card number. Mask the rest. Empty if redacted."},
            "statement_period_start": {"type": "string", "description": "YYYY-MM-DD"},
            "statement_period_end": {"type": "string"},
            "currency": {"type": "string", "description": "ISO 4217."},
            "opening_balance": {"type": "string"},
            "closing_balance": {"type": "string", "description": "For CC statements this is the 'New Balance' / 'Statement Balance'."},
            # Credit-card-specific fields — empty for plain bank statements.
            "payment_due_date": {"type": "string", "description": "YYYY-MM-DD. CC statements only. Empty for bank statements."},
            "minimum_payment_due": {"type": "string", "description": "CC statements only. Empty for bank statements."},
            "previous_balance": {"type": "string", "description": "CC statements only."},
            "payments_received": {"type": "string", "description": "Total credits/payments since the last statement. CC statements only."},
            "top_transactions": {
                "type": "array",
                "items": _TXN_ITEM,
                "description": (
                    "EVERY transaction line on the statement, up to 100. Capture all of them — "
                    "do not skip any, do not 'pick the largest', do not summarise. "
                    "For credit-card statements: every charge AND every payment received. "
                    "For bank statements: every debit AND every credit. "
                    "Set direction='debit' for money leaving the account / new charges, "
                    "'credit' for money coming in / payments. Empty array only if the doc "
                    "shows balances but no individual transactions (rare)."
                ),
            },
        },
    ),

    # ── Policies / procedures ───────────────────────────────────────────
    "policy_or_procedure": FactSchema(
        label="Internal policy or procedure",
        description="Extract the structured facts of an internal policy, code of conduct, runbook, or procedure document.",
        fields={
            "policy_title": {"type": "string"},
            "effective_date": {"type": "string", "description": "YYYY-MM-DD; the date the policy takes effect."},
            "last_reviewed_date": {"type": "string", "description": "YYYY-MM-DD; most recent review date."},
            "next_review_date": {"type": "string"},
            "owner": {"type": "string", "description": "Person or function responsible (CISO, Head of Legal, Engineering Manager, etc)."},
            "approver": {"type": "string"},
            "scope": {"type": "string", "description": "One-sentence scope statement — who/what this applies to."},
            "key_requirements": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Up to 8 one-line bullets capturing the policy's required behaviours / controls.",
            },
            "related_standards": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Standards / control IDs the policy references — 'ISO 27001 A.5', 'SOC 2 CC6.1', 'NIST 800-53 AC-2', 'GDPR Art. 32', etc.",
            },
        },
    ),

    # ── UNIVERSAL · auto-discovered schema for any doc type ─────────────
    # This is the fallback when the classifier returns a doc_type that
    # has no curated schema (a portfolio statement, mortgage doc, K-1
    # form, lease agreement, hospital bill, etc). The LLM identifies the
    # doc kind itself and slots its content into typed arrays that the
    # rest of the system (facts_det / chat / RAG / artifact materializer)
    # already knows how to render.
    #
    # Anti-goal: do NOT replace curated schemas. Curated schemas (invoice,
    # bank_statement, insurance_certificate, etc) have tighter prompts
    # and validated field names that work better than this generic one.
    # Universal is the safety net for the long tail of types we haven't
    # seen yet or that aren't worth curating individually.
    #
    # M44.P8.B (planned) · a nightly job analyzes universal extractions,
    # finds doc_types appearing 5+ times with consistent field shapes,
    # and emits a suggested curated FactSchema the operator can adopt.
    "universal": FactSchema(
        label="Document (universal extractor)",
        description=(
            "Identify what kind of document this is and extract its most "
            "useful structured facts. Works for any doc type. Use the typed "
            "slots provided so downstream surfaces (chat, search, summary) "
            "can render the data uniformly."
        ),
        fields={
            "detected_doc_type": {
                "type": "string",
                "description": (
                    "Your specific human-readable doc-type label in "
                    "snake_case. Be precise: 'mortgage_statement' not "
                    "'document', 'tax_form_w2' not 'tax_form', "
                    "'brokerage_portfolio_statement' not 'statement'."
                ),
            },
            "detected_doc_subtype": {
                "type": "string",
                "description": "Optional finer-grained label. Empty if doc_type already specific enough.",
            },
            "title": {
                "type": "string",
                "description": "The doc's title / header line (e.g. 'Form 1099-DIV', 'Notice of Lease Renewal').",
            },
            "issuer": {
                "type": "string",
                "description": "Who issued / produced this document (organisation or person).",
            },
            "issuer_address": {
                "type": "string",
            },
            "subject_or_recipient": {
                "type": "string",
                "description": "Whom the document is FOR / about (the primary subject or recipient).",
            },
            "parties": {
                "type": "array",
                "items": _PARTY_ITEM,
                "description": "All other parties named in the document with their role.",
            },
            "primary_date": {
                "type": "string",
                "description": "The single most-important date on the document (issue / effective / statement / due date). YYYY-MM-DD when possible.",
            },
            "dates": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "label": {"type": "string", "description": "What this date is for · snake_case (issue_date / due_date / statement_period_start / closing_date / etc)."},
                        "value": {"type": "string", "description": "The date as printed; YYYY-MM-DD when possible."},
                    },
                    "required": ["label", "value"],
                },
                "description": "All other key dates with what each represents. Up to 12.",
            },
            "primary_amount": {
                "type": "string",
                "description": "The headline amount (total due / balance / sum insured / principal). Include currency.",
            },
            "amounts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "label": {"type": "string", "description": "What this amount represents · snake_case."},
                        "value": {"type": "string", "description": "Amount with currency (e.g. 'USD 1,250.00')."},
                    },
                    "required": ["label", "value"],
                },
                "description": "All other relevant monetary amounts. Up to 16.",
            },
            "identifiers": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "label": {"type": "string", "description": "Type of ID · snake_case (account_number / loan_number / case_number / ssn / ein / mrn / etc)."},
                        "value": {"type": "string"},
                    },
                    "required": ["label", "value"],
                },
                "description": "Account numbers, IDs, reference numbers, case numbers, etc.",
            },
            "key_facts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "label": {"type": "string", "description": "Field name · snake_case (e.g. interest_rate / loan_term_months / coverage_limit / holding_share_class)."},
                        "value": {"type": "string"},
                    },
                    "required": ["label", "value"],
                },
                "description": "Any other typed fact worth knowing. Include domain-specific values here (rates, terms, classifications). Up to 24.",
            },
            "key_text_points": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Up to 8 verbatim one-line quotes capturing the most important clauses / obligations / disclosures.",
            },
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Up to 6 short topical tags for search (e.g. 'mortgage', 'fixed_rate', 'jumbo_loan', 'florida').",
            },
            # M46 · adaptive tabular capture — the universal way to hold the
            # REPEATING rows any document type carries. This is what lets a
            # statement keep its transactions, an invoice its line items, a
            # medical report its test results, a lease its payment schedule, a
            # portfolio its holdings — without a curated per-type schema.
            "records": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "kind": {"type": "string", "description": "Row type · snake_case (transaction / line_item / test_result / payment_schedule / coverage_item / holding / charge / clause). Pick what fits this document."},
                        "date": {"type": "string", "description": "Row date if any · YYYY-MM-DD when possible; empty string if none."},
                        "description": {"type": "string", "description": "The row's main text — merchant/payee, item name, test name, clause summary, etc."},
                        "amount": {"type": "string", "description": "Monetary value with currency if any (e.g. 'USD -42.10'); empty if the row isn't monetary."},
                        "reference": {"type": "string", "description": "Any per-row identifier — cheque no., ref, SKU, code; empty if none."},
                        "attributes": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {"label": {"type": "string"}, "value": {"type": "string"}},
                                "required": ["label", "value"],
                            },
                            "description": "Any extra per-row fields (running_balance / quantity / unit_price / result_value / units / status). Empty array if none.",
                        },
                    },
                    "required": ["description"],
                },
                "description": "EVERY repeating tabular row in the document — bank/card TRANSACTIONS, invoice LINE ITEMS, medical TEST RESULTS, lease PAYMENT SCHEDULE, portfolio HOLDINGS, itemised CHARGES, etc. Extract every row you can read (up to 200). Leave empty only if the document genuinely has no tabular/repeating data.",
            },
        },
    ),

    # ── Insurance certificates ──────────────────────────────────────────
    # One schema covers all insurance kinds (motor / property / liability
    # / health / life / marine / professional indemnity / D&O / cyber).
    # The doc_subtype field tells us which kind it is; common fields
    # below capture insurer, parties, dates, sums, premium, exclusions.
    "insurance_certificate": FactSchema(
        label="Insurance certificate / cover note",
        description=(
            "Extract structured facts from an insurance certificate, cover note, "
            "or insurance schedule. Common types include motor (third-party / "
            "comprehensive), property, liability, health, life, marine, "
            "professional indemnity, D&O, and cyber insurance."
        ),
        fields={
            "doc_subtype": {
                "type": "string",
                "enum": [
                    "motor_third_party", "motor_comprehensive",
                    "property", "liability", "health", "life",
                    "marine", "professional_indemnity", "directors_officers",
                    "cyber", "travel", "other",
                ],
                "description": "Best-guess of which kind of insurance this covers.",
            },
            "insurer_name": {
                "type": "string",
                "description": "The insurance company issuing the policy (e.g. 'Etiqa', 'Income', 'AIG', 'Allianz').",
            },
            "policy_number": {
                "type": "string",
                "description": "The policy number / certificate number / cover note number. Quote verbatim.",
            },
            "policyholder_name": {
                "type": "string",
                "description": "Name of the insured party / policyholder (person or company).",
            },
            "insured_party_address": {
                "type": "string",
                "description": "Address of the policyholder.",
            },
            "effective_date": {
                "type": "string",
                "description": "When coverage starts. YYYY-MM-DD when possible.",
            },
            "expiry_date": {
                "type": "string",
                "description": "When coverage ends. YYYY-MM-DD when possible.",
            },
            "sum_insured": {
                "type": "string",
                "description": "The sum insured / total cover amount with currency (e.g. 'MYR 250,000', 'USD 1,000,000'). Empty if not stated.",
            },
            "premium": {
                "type": "string",
                "description": "Premium amount with currency.",
            },
            "vehicle_registration": {
                "type": "string",
                "description": "Motor insurance only · vehicle registration plate (e.g. 'JQ 5489'). Empty for non-motor.",
            },
            "vehicle_make_model": {
                "type": "string",
                "description": "Motor insurance only · make / model / year.",
            },
            "vehicle_chassis": {
                "type": "string",
                "description": "Motor insurance only · chassis / VIN number.",
            },
            "vehicle_engine": {
                "type": "string",
                "description": "Motor insurance only · engine number.",
            },
            "coverage_summary": {
                "type": "string",
                "description": "One-sentence description of what is covered.",
            },
            "exclusions": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Up to 8 bullets of key exclusions / 'limitations as to use' / what is NOT covered.",
            },
            "governing_law": {
                "type": "string",
                "description": "Acts / regulations / jurisdictions referenced (e.g. 'Road Transport Act 1987 (Malaysia)', 'Sec. 31 of the Motor Vehicles Insurance Act').",
            },
            "agent_or_broker": {
                "type": "string",
                "description": "Name of the broker / intermediary if visible.",
            },
        },
    ),

    # ── ID documents (passport, national ID, driver licence) ────────────
    # Text-based path: operates on the OCR'd chunks. Sensitive fields are
    # NOT masked here — the KYC vision extractor that runs at match-time
    # owns the masking policy (e.g. last-4 only). This schema is for the
    # reviewer-facing Key Facts card.
    "id_document": FactSchema(
        label="Government-issued photo ID (passport / national ID / driver licence)",
        description="Extract identifying fields from an OCR transcript of a passport, national ID card, or driver licence — any country.",
        fields={
            "doc_subtype": {
                "type": "string",
                "enum": ["passport", "national_id", "driver_licence", "residency_permit", "other"],
            },
            "issuing_country": {"type": "string", "description": "Full country name as printed (e.g. 'Republic of Singapore', 'United Kingdom')."},
            "issuing_country_code": {"type": "string", "description": "ISO 3166-1 alpha-3 (e.g. SGP, USA, GBR) if visible — usually in the MRZ."},
            "holder_name": {"type": "string", "description": "Full name as printed (surname + given names, in document order)."},
            "sex": {"type": "string", "enum": ["M", "F", "X", ""], "description": "As printed on the document. Empty string if not visible."},
            "date_of_birth": {"type": "string", "description": "YYYY-MM-DD. Empty if not visible."},
            "place_of_birth": {"type": "string", "description": "Country/place of birth as printed. This is NOT nationality."},
            "race": {"type": "string", "description": "Race / ethnicity if the card prints it (e.g. Singapore NRIC, Malaysian MyKad: Chinese, Malay, Indian, Eurasian, …). This is NOT nationality — do not put it in the nationality field."},
            "nationality": {"type": "string", "description": "Nationality / citizenship of the holder. NOT race/ethnicity and NOT place of birth. If nationality is not explicitly printed (many national ID cards omit it), infer it from the issuing country/authority — a national ID is issued by the country of citizenship (e.g. Republic of Singapore → 'Singaporean'). Leave blank only if it truly cannot be determined."},
            "document_number": {"type": "string", "description": "Passport/licence/ID number, in full."},
            "national_id_number": {"type": "string", "description": "Secondary national ID number if one appears alongside (e.g. Singapore NRIC under a passport)."},
            "date_of_issue": {"type": "string", "description": "YYYY-MM-DD"},
            "date_of_expiry": {"type": "string", "description": "YYYY-MM-DD. Empty if document doesn't expire."},
            "issuing_authority": {"type": "string"},
            "mrz_line_1": {"type": "string", "description": "First line of the machine-readable zone if present."},
            "mrz_line_2": {"type": "string", "description": "Second line of the MRZ if present."},
            "is_expired": {"type": "boolean", "description": "True iff date_of_expiry is before today. Use empty string for date_of_expiry to disable this check."},
        },
    ),

    # ── Certificates (ISO, SOC2, incorporation, etc) ────────────────────
    "certificate": FactSchema(
        label="Compliance certificate / attestation",
        description="Extract the structured facts of a compliance certificate, attestation, incorporation certificate, or third-party audit report.",
        fields={
            "certificate_type": {
                "type": "string",
                "description": "Short phrase: 'ISO 27001 Certificate', 'SOC 2 Type II Report', 'Certificate of Incorporation', 'Penetration Test Report', etc.",
            },
            "issuing_authority": {"type": "string", "description": "Auditor / certification body / registry."},
            "subject_org": {"type": "string", "description": "The organisation being certified."},
            "certificate_number": {"type": "string"},
            "issue_date": {"type": "string", "description": "YYYY-MM-DD"},
            "expiry_date": {"type": "string", "description": "YYYY-MM-DD; empty if not applicable (e.g. incorporation certificates never expire)."},
            "scope": {"type": "string", "description": "Scope statement — what activities, locations, products, etc."},
            "standards_covered": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Standards / control sets covered — 'ISO/IEC 27001:2022', 'SOC 2', 'TSP CC1-CC8', etc.",
            },
        },
    ),

    # ── Business profile (ACRA bizfile / Companies House / Sec-of-State) ──
    # The KYB workhorse — pulled from any official entity-registration
    # extract or business profile. Same field skeleton works for:
    #   * Singapore  · ACRA bizfile / Entity Profile (UEN)
    #   * UK         · Companies House overview (Company number)
    #   * USA        · Secretary of State entity profile (state filing #)
    #   * India      · MCA Master Data (CIN)
    #   * Hong Kong  · CR / Annual Return (CR number)
    # The label is intentionally registry-agnostic — the registry name
    # (ACRA / Companies House / etc) is captured as a separate field.
    "business_profile": FactSchema(
        label="Business profile / entity registration extract",
        description=(
            "Extract structured facts from an official entity-registration "
            "extract or business profile — Singapore ACRA bizfile, UK "
            "Companies House overview, US Secretary-of-State filing, "
            "India MCA Master Data, HK Companies Registry, or equivalent. "
            "The audited entity is the SUBJECT of the document."
        ),
        fields={
            "registration_number": {
                "type": "string",
                "description": "The unique entity identifier as printed — UEN (Singapore), Company Number (UK), Sec-of-State filing number (US state), CIN (India MCA), CR Number (Hong Kong). Verbatim, including any prefix letters.",
            },
            "entity_name": {"type": "string", "description": "Registered legal name as printed."},
            "entity_type": {
                "type": "string",
                "description": "Legal form / vehicle as printed — 'Sole Proprietorship', 'Private Limited Company', 'LLC', 'LLP', 'Limited Partnership', 'Public Company Limited by Shares', etc.",
            },
            "entity_status": {
                "type": "string",
                "description": "Operational status as printed — 'Live', 'Active', 'Dormant', 'Struck off', 'Wound up', 'Dissolved', 'Cancelled', etc.",
            },
            "status_date": {"type": "string", "description": "YYYY-MM-DD. Date the current status took effect (e.g. dissolution date, status confirmation date). Empty if not stated."},
            "registration_date": {"type": "string", "description": "YYYY-MM-DD. Original incorporation / registration date."},
            "registry_name": {
                "type": "string",
                "description": "Who maintains this registration — 'ACRA' (Singapore), 'Companies House' (UK), 'Delaware Secretary of State', 'MCA' (India), 'Companies Registry' (HK), etc.",
            },
            "jurisdiction": {"type": "string", "description": "Country or sub-national jurisdiction of registration (e.g. 'Singapore', 'United Kingdom', 'Delaware, USA', 'Hong Kong SAR')."},
            "primary_business_activity": {
                "type": "string",
                "description": "Primary activity / SIC / SSIC / NAICS code description as printed. Include the code in brackets if shown (e.g. '56200 - Food caterers').",
            },
            "secondary_business_activities": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Up to 5 additional business activity descriptions or codes. Empty if not listed.",
            },
            "registered_address": {"type": "string", "description": "Registered office address as printed (one string, address lines comma-separated)."},
            "office_address": {"type": "string", "description": "Operating / principal place of business address if different from registered. Empty if same or not listed."},
            "email_address": {"type": "string", "description": "Contact email if printed on the profile. Empty if not listed."},
            "phone": {"type": "string", "description": "Contact phone if printed. Empty if not listed."},
            "officers": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "role": {"type": "string", "description": "Director / Secretary / Owner / Partner / Manager / etc as printed."},
                        "appointment_date": {"type": "string", "description": "YYYY-MM-DD; empty if not shown."},
                    },
                    "required": ["name"],
                },
                "description": "All listed officers / directors / partners / owners / authorised representatives. Cap at 20.",
            },
            "share_capital_amount": {"type": "string", "description": "Paid-up / issued share capital as printed (with currency). Empty for non-capital entities like sole proprietorships."},
            "share_capital_currency": {"type": "string", "description": "ISO 4217 if shown alongside share capital."},
            "last_updated": {"type": "string", "description": "YYYY-MM-DD. The 'last updated' / 'as at' date the registry stamps the extract with. Critical for reviewer to know freshness."},
        },
    ),
    # ── Résumé / CV ─────────────────────────────────────────────────────
    # Candidate profile. The universal extractor collapses education into a single
    # `highest_education` key_fact and never captures the individual SSC/HSC/degree
    # rows — this schema puts them in a structured education[] array so the Fields/JSON
    # view shows the full academic record. Kept deliberately tolerant of layout
    # (Indian SSC/HSC %, US GPA, UK classifications) — grades stay as PRINTED strings.
    "resume": FactSchema(
        label="Résumé / CV",
        description=(
            "Extract the structured facts of a candidate résumé / CV / curriculum vitae — "
            "personal details, education history, work experience, skills, and certifications. "
            "Capture EVERY education entry as a separate row in education[] (school-leaving exams "
            "like SSC/HSC/10th/12th AND diplomas AND degrees), not just the highest. For each "
            "education row, ALWAYS include the marks/result if the résumé prints any — copy the "
            "percentage, CGPA, GPA, grade or class VERBATIM into the `score` field (e.g. "
            "'61.47%', '8.4 CGPA', 'First Class'). Do not omit marks that are shown, and do not "
            "convert between formats. Use the field names exactly as given: "
            "qualification, institution, field_of_study, year, score."
        ),
        fields={
            "full_name": {"type": "string", "description": "Candidate's full name as printed at the top."},
            "email": {"type": "string", "description": "Contact email if shown. Empty if not."},
            "phone": {"type": "string", "description": "Contact phone if shown. Empty if not."},
            "location": {"type": "string", "description": "City / country of residence if shown. Empty if not."},
            "headline": {"type": "string", "description": "Professional title / objective / summary line if present (e.g. 'Full-Stack Developer'). Empty if none."},
            "highest_education": {"type": "string", "description": "One-line summary of the highest qualification (e.g. 'B.E. Computer Engineering, 2023'). Back-compat convenience — the full list goes in education[]."},
            "total_experience_years": {"type": "string", "description": "Total professional experience in years if stated or clearly computable, as printed (e.g. '5', '5+'). Empty if a fresher / not shown."},
            "education": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "qualification": {"type": "string", "description": "The exam / degree / level as printed — 'SSC', 'HSC', '10th', '12th', 'Diploma', 'B.E.', 'B.Sc', 'MBA', etc. Keep the candidate's own label."},
                        "institution": {"type": "string", "description": "School / college / university / board name as printed."},
                        "field_of_study": {"type": "string", "description": "Stream / major / specialisation if shown (e.g. 'Computer Engineering', 'Science'). Empty if not."},
                        "year": {"type": "string", "description": "Year or year-range of completion as printed (e.g. '2023', '2019-2023'). Empty if not shown."},
                        "score": {"type": "string", "description": "Result EXACTLY as printed — percentage, CGPA, GPA, grade, or classification (e.g. '67.16%', '8.4 CGPA', 'First Class', '3.8 GPA'). Do NOT convert or normalise. Empty if not shown."},
                    },
                    "required": ["qualification"],
                },
                "description": "Every education / qualification entry as its own row, most recent first. Include school-leaving exams (SSC/HSC/10th/12th) as well as diplomas and degrees — do not drop the lower ones. CRITICAL: copy each row's marks into `score` whenever a percentage / CGPA / GPA / grade is printed for it (e.g. an HSC row showing 61.47% → score='61.47%'). Example row: {\"qualification\": \"HSC\", \"institution\": \"R. H. Kapadiya School\", \"year\": \"2019-2020\", \"score\": \"61.47%\"}.",
            },
            "experience": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "description": "Job title / role as printed."},
                        "organization": {"type": "string", "description": "Employer / company name."},
                        "start_date": {"type": "string", "description": "As printed (e.g. 'Jun 2021', '2021'). Empty if not shown."},
                        "end_date": {"type": "string", "description": "As printed, or 'Present' / 'Current'. Empty if not shown."},
                        "summary": {"type": "string", "description": "One-line summary of responsibilities / achievements. Empty if none."},
                    },
                    "required": ["title"],
                },
                "description": "Work experience entries, most recent first. Cap at 20. Empty array for a fresher.",
            },
            "skills": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Listed skills / technologies / competencies. Cap at 40. Empty if none listed.",
            },
            "certifications": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Certifications / licences / courses as printed (e.g. 'AWS Certified Solutions Architect'). Empty if none.",
            },
            "languages": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Spoken/written languages if listed. Empty if none.",
            },
        },
    ),
}
