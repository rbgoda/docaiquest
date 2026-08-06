"""PII redaction tests — account-number detection, value-stable tokens, and
the round-trip. Locks the 2026-06-13 gap fixes (bank account numbers reaching
the LLM; same value getting different tokens → guardrail false-flags).

Run:  cd backend && pytest tests/test_pii.py -v
"""
from __future__ import annotations

from app.pii import detokenize, redact


def test_account_number_is_redacted_when_labelled():
    r = redact("Beneficiary Account number: 288-900557 Bank: DBS")
    assert "288-900557" not in r.text
    assert "[ACCOUNT_1]" in r.text
    assert detokenize(r.text, r.mapping) == "Beneficiary Account number: 288-900557 Bank: DBS"


def test_invoice_number_and_amount_not_redacted():
    # Only ACCOUNT-labelled numbers; invoice/PO numbers + amounts must survive
    # (the model needs to reason over amounts).
    r = redact("Invoice number: INV-2026-001 Total Amount Due: 4080.00")
    assert "INV-2026-001" in r.text
    assert "4080.00" in r.text


def test_value_stable_tokens_same_value_same_token():
    # The same value appearing twice (e.g. in evidence AND answer the guardrail
    # compares) MUST map to one token — else the reviewer false-flags a match.
    r = redact("Account no: 288-900557 ... A/C 288-900557 ... mail a@b.com x a@b.com")
    assert r.text.count("[ACCOUNT_1]") == 2
    assert "[ACCOUNT_2]" not in r.text
    assert r.text.count("[EMAIL_1]") == 2


def test_standard_identifiers_round_trip():
    raw = "NRIC S1234567A, email j@x.io, phone +65 9180 9136, Aadhaar 1234 5678 9012"
    r = redact(raw)
    for tok in ("[NRIC_1]", "[EMAIL_1]", "[PHONE_E164_1]", "[AADHAAR_1]"):
        assert tok in r.text
    assert detokenize(r.text, r.mapping) == raw


def test_extra_terms_redacts_extracted_entities():
    r = redact("Pay to Smart Audit Pte Ltd today", extra_terms=[("person", "Smart Audit Pte Ltd")])
    assert "Smart Audit Pte Ltd" not in r.text
    assert "[PERSON_1]" in r.text


def test_redact_names_off_keeps_names_masks_ids():
    # Names are the search key — with redact_names=False they stay visible so
    # "find documents for Kalyani Goda" works, but sensitive IDs still mask.
    s = "Applicant KALYANI GODA, NRIC S1234567A, Account number: 288-900557, email k@x.io"
    out = redact(s, redact_names=False).text
    assert "KALYANI GODA" in out                 # name visible
    assert "S1234567A" not in out and "[NRIC_1]" in out
    assert "288-900557" not in out and "[ACCOUNT_1]" in out
    assert "k@x.io" not in out and "[EMAIL_1]" in out


def test_redact_names_on_masks_names():
    out = redact("Applicant: KALYANI GODA", redact_names=True).text
    assert "KALYANI GODA" not in out and "[PERSON_1]" in out


def test_swift_is_label_anchored_only():
    # Plain 8-letter uppercase words must NOT be treated as SWIFT codes.
    assert redact("EVIDENCE DOCUMENT APPROVED").text == "EVIDENCE DOCUMENT APPROVED"
    # A labelled SWIFT/BIC IS redacted.
    r = redact("SWIFT: DBSSSGSG and BIC HSBCSGS2")
    assert "DBSSSGSG" not in r.text and "[SWIFT_BIC_1]" in r.text
