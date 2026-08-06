"""Schema-shaped JSON: render a document's extracted values in its approved schema's shape.

- If the doc's type has an APPROVED schema_library entry, the record has exactly that schema's
  fields (in order), each filled from the extraction — directly when the key matches, else
  best-effort from the universal envelope (parties/amounts/dates/identifiers) — with the field's
  source marked and missing fields shown as null. A conformance summary reports coverage.
- Otherwise falls back to the universal envelope as-is.

Powers the JSON tab's "Schema" view + export. No LLM.
"""
from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_current_tenant
from app.orm import Document, SchemaLibrary


def _norm(s) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


# JSON-Schema definition-metadata words. Some approved schemas accidentally flattened
# a field-definition's own metadata into their top-level field map (e.g. a field's
# 'required'/'description'/'type' surfaced as if it were its own field), which then
# rendered as permanently-'missing' phantom rows in the Schema view. A REAL field
# always has a dict definition; a reserved word carrying a non-dict value is a leak.
_RESERVED_META = {"required", "description", "type", "properties", "enum",
                  "format", "items", "title", "examples", "default"}


def _clean_fields(fields: dict) -> dict:
    """Drop leaked definition-metadata keys, keeping every genuine field (incl. a real
    field that happens to be named 'type' etc. — those carry a dict definition)."""
    return {k: v for k, v in fields.items()
            if not (k in _RESERVED_META and not isinstance(v, dict))}


def _items(values: dict, key: str) -> list:
    v = values.get(key)
    return v if isinstance(v, list) else []


def _pick(items: list, *, value_keys, label_keys, want: list[str]):
    """From a list of dict items, return the value whose label matches any `want` keyword."""
    wantn = [_norm(w) for w in want]
    for it in items:
        if not isinstance(it, dict):
            continue
        label = _norm(next((it.get(k) for k in label_keys if it.get(k)), ""))
        if any(w in label for w in wantn):
            val = next((it.get(k) for k in value_keys if it.get(k) not in (None, "")), None)
            if val not in (None, ""):
                return val
    return None


def _label_lookup(values: dict, key: str):
    """Generic: match a schema field against any LABELED item in the universal envelope
    (key_facts/identifiers/dates/amounts/parties) or a fuzzy top-level scalar key. The universal
    extractor already labels its facts ('date_of_birth', 'nric_number', 'sex', 'country_of_birth',
    …), so this populates most schema fields for ANY doc type, not just invoices."""
    kl = _norm(key)
    if len(kl) < 3:
        return None
    for arr, vkeys in (("key_facts", ("value",)), ("identifiers", ("value", "id")),
                       ("dates", ("value", "date")), ("amounts", ("value", "amount")),
                       ("parties", ("name", "value"))):
        for it in _items(values, arr):
            if not isinstance(it, dict):
                continue
            label = _norm(it.get("label") or it.get("role") or it.get("type") or "")
            if label and (label == kl or (len(kl) >= 4 and (kl in label or label in kl))):
                val = next((it.get(vk) for vk in vkeys if it.get(vk) not in (None, "")), None)
                if val not in (None, ""):
                    return val
    for fk, fv in values.items():
        if isinstance(fv, str) and fv.strip():
            fkn = _norm(fk)
            if fkn == kl or (len(kl) >= 4 and (kl in fkn or fkn in kl)):
                return fv
    return None


def _from_envelope(key: str, values: dict):
    """Best-effort pull a schema field's value from the universal envelope."""
    kl0 = key.lower()
    # 0. ID fields FIRST — before the fuzzy label lookup, which otherwise substring-matches
    #    a 'patient' party ROLE to a 'patient_id' field and returns the person's NAME.
    if kl0.endswith("_id") or kl0 == "id" or any(w in kl0 for w in ("_no", "pid", "mrn")):
        _idents = _items(values, "identifiers")
        hit = _pick(_idents, want=["id", "pid", "patient", "number", "reference", "policy", "account"],
                    value_keys=("value", "id"), label_keys=("label", "type", "name"))
        if hit in (None, "") and _idents and isinstance(_idents[0], dict):
            hit = _idents[0].get("value")
        if hit not in (None, ""):
            return hit
    # 1. Generic label / fuzzy-key match — the universal extractor's own labels cover most fields.
    v = _label_lookup(values, key)
    if v not in (None, ""):
        return v
    kl = key.lower()
    parties = _items(values, "parties")
    amounts = _items(values, "amounts")
    dates = _items(values, "dates")
    idents = _items(values, "identifiers")
    # 2. Person-name fields → the subject/holder or a party. Broad: patient_name /
    #    customer_name / any *_name / member / recipient. Exclude company/file names
    #    and issuer-side labels (lab/hospital/vendor — handled below).
    _issuer_word = any(w in kl for w in ("lab", "laborat", "hospital", "clinic", "issuer",
                                         "vendor", "supplier", "seller", "provider", "company", "file"))
    # NOT an id/number field — 'patient_id' contains 'patient' but is an identifier.
    _id_word = kl.endswith("_id") or any(w in kl for w in ("_no", "number", "pid", "mrn", "_id_"))
    if (not _issuer_word and not _id_word and (
            any(w in kl for w in ("full_name", "holder", "cardholder", "account_name", "person_name",
                                  "patient", "subject", "recipient", "member", "beneficiary"))
            or kl in ("name",) or kl.endswith("_name"))):
        return values.get("subject_or_recipient") or _pick(
            parties, want=["holder", "subject", "cardholder", "name", "self", "patient"],
            value_keys=("name", "value"), label_keys=("role", "label", "type"))
    P = dict(value_keys=("name", "value"), label_keys=("role", "label", "type"))
    A = dict(value_keys=("value", "amount"), label_keys=("label", "type", "name"))
    D = dict(value_keys=("value", "date"), label_keys=("label", "type", "name"))
    ID = dict(value_keys=("value", "id"), label_keys=("label", "type", "name"))

    # Party NAMES only — not addresses/emails/phones (the universal envelope has names, not those).
    is_name = not any(w in kl for w in ("address", "email", "phone", "tel", "fax", "contact"))
    if is_name and any(w in kl for w in ("seller", "issuer", "vendor", "supplier", "payee", "from_",
                                         "lab", "laborat", "reporting", "hospital", "clinic", "provider")):
        return values.get("issuer") or _pick(parties, want=["issuer", "seller", "vendor", "supplier",
                                                             "from", "payee", "lab", "laboratory",
                                                             "provider", "hospital"], **P)
    if is_name and any(w in kl for w in ("buyer", "client", "customer", "bill", "recipient", "payer")):
        return _pick(parties, want=["buyer", "client", "customer", "bill", "recipient", "payer", "to"], **P)
    # Date of birth — a birth-labelled value ONLY; must NOT fall through to the generic
    # 'date' branch below, which would wrongly return the doc's primary/test date. DOB may
    # live in `dates`, but extractors commonly label it in `key_facts`/`identifiers` as 'dob'
    # (which doesn't substring-match the field name 'date_of_birth', so _label_lookup misses).
    if any(w in kl for w in ("birth", "dob")):
        _dob_want = ["birth", "dob", "date of birth"]
        return (_pick(dates, want=_dob_want, **D)
                or _pick(_items(values, "key_facts"), want=_dob_want,
                         value_keys=("value",), label_keys=("label", "type", "name"))
                or _pick(idents, want=_dob_want, **ID))
    if kl in ("total", "grand_total", "amount_due", "amount", "total_amount", "balance_due"):
        return values.get("primary_amount") or _pick(amounts, want=["grand total", "total", "amount due", "balance"], **A)
    if "subtotal" in kl or kl == "sub_total":
        return _pick(amounts, want=["subtotal", "sub total"], **A)
    if kl == "tax" or "gst" in kl or "vat" in kl or "tax_amount" in kl:
        return _pick(amounts, want=["tax", "gst", "vat"], **A)
    if "discount" in kl:
        return _pick(amounts, want=["discount"], **A)
    if "date" in kl:
        want = ["due"] if "due" in kl else (["issue", "invoice"] if any(w in kl for w in ("issue", "invoice")) else [])
        return (_pick(dates, want=want, **D) if want else None) or values.get("primary_date") or _pick(dates, want=[""], **D)
    if (any(w in kl for w in ("number", "_no", "reference", "invoice_id", "receipt",
                              "patient_id", "pid", "mrn", "policy", "account"))
            or kl.endswith("_id") or kl == "id"):
        return (_pick(idents, want=["invoice", "receipt", "reference", "number", "id",
                                    "pid", "patient", "policy", "account"], **ID)
                or (idents[0].get("value") if idents and isinstance(idents[0], dict) else None))
    # List/record fields (lab test_results, invoice line_items, statement transactions) →
    # the universal records array so the schema view shows them instead of 'missing'.
    if any(w in kl for w in ("result", "test", "finding", "observation", "line_item",
                             "lineitem", "record", "transaction", "item")):
        recs = values.get("records") or values.get("top_transactions")
        if recs:
            return recs
    return None


def schema_shaped(db: Session, doc: Document) -> dict:
    ef = doc.extracted_fields if isinstance(doc.extracted_fields, dict) else {}
    values = ef.get("fields") if isinstance(ef.get("fields"), dict) else {}
    doc_type = doc.doc_type or ef.get("doc_type")

    row = None
    if doc_type:
        row = db.scalar(select(SchemaLibrary).where(
            SchemaLibrary.tenant_id == get_current_tenant(),
            SchemaLibrary.type_slug == doc_type,
            SchemaLibrary.status == "approved").order_by(SchemaLibrary.version.desc()))

    fields = _clean_fields(row.fields) if (row and isinstance(row.fields, dict)) else {}
    if not fields:
        return {
            "docType": doc_type, "schemaSource": "universal",
            "schemaLabel": "Universal extraction (no approved type schema)",
            "record": values, "fieldSources": {},
            "conformance": {"total": len(values), "populated": len(values),
                            "missing": [], "missingRequired": []},
        }

    record: dict = {}
    sources: dict = {}
    missing: list[str] = []
    missing_required: list[str] = []
    populated = 0
    for key, defn in fields.items():
        required = isinstance(defn, dict) and defn.get("required")
        v = values.get(key)
        if v not in (None, "", [], {}):
            record[key], sources[key] = v, "extracted"
            populated += 1
            continue
        derived = _from_envelope(key, values)
        if derived not in (None, "", [], {}):
            record[key], sources[key] = derived, "derived"
            populated += 1
            continue
        record[key], sources[key] = None, "missing"
        missing.append(key)
        if required:
            missing_required.append(key)

    return {
        "docType": doc_type, "schemaSource": "library", "schemaLabel": row.label,
        "record": record, "fieldSources": sources,
        "conformance": {"total": len(fields), "populated": populated,
                        "missing": missing, "missingRequired": missing_required},
    }
