"""M44.P13 PR1 · tests for the skeletonizer — the federated-learning privacy
barrier. All DB-free; always run in CI. The cardinal property under test:
a skeleton NEVER carries values, identities, PII, tenant ids, or doc ids —
and when that can't be guaranteed, skeletonize() REFUSES (returns None)."""
from __future__ import annotations

from app.services import skeletonizer as sk


# ── extraction_correction ─────────────────────────────────────────────────

def test_extraction_correction_keeps_field_names_only():
    out = sk.skeletonize(
        "extraction_correction",
        doc_type="Insurance_Certificate",
        pattern={"wrong_field": "fields.policy_number", "should_be": "fields.policy_no", "tenant_id": "acme"},
    )
    assert out == {
        "kind": "extraction_correction",
        "doc_type": "insurance_certificate",          # normalized, lowercased
        "pattern": {"wrong_field": "fields.policy_number", "should_be": "fields.policy_no"},
    }
    # the stray tenant_id must NOT survive the allow-list
    assert "tenant_id" not in out["pattern"]


def test_extraction_correction_requires_wrong_field():
    assert sk.skeletonize("extraction_correction", doc_type="invoice", pattern={"should_be": "x"}) is None


def test_extraction_correction_refuses_non_dict_pattern():
    assert sk.skeletonize("extraction_correction", doc_type="invoice", pattern="oops") is None


def test_extraction_correction_refuses_pii_in_field_value():
    # a value that trips PII (email) in what should be a field NAME → refuse
    out = sk.skeletonize(
        "extraction_correction",
        doc_type="invoice",
        pattern={"wrong_field": "contact_for_jane@acme.com"},
    )
    assert out is None


# ── agent_skill ────────────────────────────────────────────────────────────

def test_agent_skill_keeps_template_and_sequence():
    out = sk.skeletonize(
        "agent_skill",
        doc_type="kyc_passport",
        question_template="what is the {id_field}?",
        tool_sequence=["search_chunks", "get_extracted_field", "final_answer"],
    )
    assert out == {
        "kind": "agent_skill",
        "doc_type": "kyc_passport",
        "question_template": "what is the {id_field}?",
        "tool_sequence": ["search_chunks", "get_extracted_field", "final_answer"],
    }


def test_agent_skill_refuses_template_with_identifier():
    # a number that slipped into the template → doc-specific → refuse
    assert sk.skeletonize(
        "agent_skill",
        doc_type="invoice",
        question_template="is policy 998877 active?",
        tool_sequence=["final_answer"],
    ) is None


def test_agent_skill_refuses_pii_email_in_template():
    assert sk.skeletonize(
        "agent_skill",
        doc_type="invoice",
        question_template="email jane@acme.com about it",
        tool_sequence=["final_answer"],
    ) is None


def test_agent_skill_refuses_empty_tool_sequence():
    assert sk.skeletonize(
        "agent_skill", doc_type="invoice", question_template="what is the total?", tool_sequence=[]
    ) is None


def test_agent_skill_refuses_empty_template():
    assert sk.skeletonize(
        "agent_skill", doc_type="invoice", question_template="   ", tool_sequence=["final_answer"]
    ) is None


# ── doc_type hygiene ───────────────────────────────────────────────────────

def test_refuses_freeform_doc_type():
    # a free-form phrase (spaces) is not a controlled-vocab token → refuse
    assert sk.skeletonize(
        "agent_skill", doc_type="John Smith's insurance", question_template="what is it?",
        tool_sequence=["final_answer"],
    ) is None


def test_refuses_missing_doc_type():
    assert sk.skeletonize("extraction_correction", doc_type=None, pattern={"wrong_field": "x"}) is None


# ── dispatch ───────────────────────────────────────────────────────────────

def test_unknown_kind_refused():
    assert sk.skeletonize("entity_canonical", doc_type="invoice", canonical="Acme") is None
    assert sk.skeletonize("reflexion_answer", doc_type="invoice") is None


# ── cardinal property: no forbidden keys EVER leak ─────────────────────────

def test_skeleton_never_contains_forbidden_keys():
    forbidden = {"tenant_id", "doc_id_external", "document_pk", "pk", "value", "answer", "canonical"}
    samples = [
        sk.skeletonize("extraction_correction", doc_type="invoice",
                       pattern={"wrong_field": "fields.total", "should_be": "fields.amount"}),
        sk.skeletonize("agent_skill", doc_type="invoice", question_template="what is the total?",
                       tool_sequence=["search_chunks", "final_answer"]),
    ]
    for s in samples:
        assert s is not None
        assert forbidden.isdisjoint(s.keys())
        # nested dicts too
        for v in s.values():
            if isinstance(v, dict):
                assert forbidden.isdisjoint(v.keys())


# ── generated_schema (Move-1 PR4) ─────────────────────────────────────────

def test_generated_schema_keeps_field_names_and_types_only():
    out = sk.skeletonize(
        "generated_schema",
        doc_type="Mortgage_Statement",
        fields={
            "interest_rate": {"type": "string", "description": "leak? no — dropped"},
            "loan_term": {"type": "number", "description": "x"},
            "escrow": {"type": "weird"},   # unknown type → coerced to string
        },
    )
    assert out == {
        "kind": "generated_schema",
        "doc_type": "mortgage_statement",              # normalized
        "fields": {"interest_rate": "string", "loan_term": "number", "escrow": "string"},
    }
    # descriptions (free text) never ride along
    assert all(isinstance(v, str) for v in out["fields"].values())


def test_generated_schema_rejects_spacey_doc_type():
    assert sk.skeletonize("generated_schema", doc_type="mortgage statement",
                          fields={"a": {"type": "string"}}) is None


def test_generated_schema_rejects_non_dict_fields():
    assert sk.skeletonize("generated_schema", doc_type="invoice", fields=["a", "b"]) is None


def test_generated_schema_drops_pii_labels_keeps_clean():
    out = sk.skeletonize(
        "generated_schema",
        doc_type="contact_sheet",
        fields={"full_name": {"type": "string"}, "person@example.com": {"type": "string"}},
    )
    # the email-shaped label trips the PII gate and is dropped; the clean one stays
    assert out is not None
    assert "full_name" in out["fields"]
    assert "person@example.com" not in out["fields"]


def test_generated_schema_empty_after_filter_is_none():
    assert sk.skeletonize("generated_schema", doc_type="x",
                          fields={"person@example.com": {"type": "string"}}) is None
