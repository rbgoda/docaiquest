"""Unit tests for the agentic workspace chat's pure helpers.

These lock in the behaviour of the field resolver, the server-side confirm
gate, the xlsx sheet-title de-duplication, and CSV rendering — the pieces a
2026-06-13 code review flagged. No DB / no LLM: fast, runs in CI.

Run:  cd backend && pytest tests/test_workspace_agent.py -v
"""
from __future__ import annotations

from app.agents import workspace_agent as wa


# ── _resolve_field_value · the HIGH-severity fuzzy-match fix ───────────────

def test_resolver_direct_and_synonym():
    f = {"primary_amount": "USD 5", "invoice_number": "INV-1"}
    assert wa._resolve_field_value(f, "invoice_number") == "INV-1"
    assert wa._resolve_field_value(f, "total") == "USD 5"        # synonym → primary_amount
    assert wa._resolve_field_value(f, "amount") == "USD 5"


def test_resolver_reads_identifiers_list_by_label():
    f = {"identifiers": [{"label": "invoice_number", "value": "INV-9"},
                         {"label": "PO Number", "value": "PO-7"}]}
    assert wa._resolve_field_value(f, "invoice_number") == "INV-9"
    assert wa._resolve_field_value(f, "po_number") == "PO-7"


def test_resolver_short_names_do_not_mismatch():
    # The bug: "id" grabbed "paid"/"valid"; "date" grabbed "mandate".
    f = {"paid": "NO", "valid_until": "2027", "mandate_ref": "M-9"}
    assert wa._resolve_field_value(f, "id") is None
    assert wa._resolve_field_value(f, "date") is None


def test_resolver_fuzzy_token_match_still_works():
    f = {"issue_date": "2026-03-14"}
    assert wa._resolve_field_value(f, "date") == "2026-03-14"   # token / *_date suffix


def test_resolver_never_returns_list_or_dict():
    f = {"line_items": [1, 2, 3], "meta": {"a": 1}}
    assert wa._resolve_field_value(f, "line_items") is None
    assert wa._resolve_field_value(f, "meta") is None


# ── _confirm_allowed · server-side confirm gate ───────────────────────────

def test_confirm_requires_affirmation_and_pending_preview():
    prior = [{"role": "ai", "text": "Create group 'X'? Reply yes to confirm."}]
    assert wa._confirm_allowed("yes please", prior) is True
    assert wa._confirm_allowed("go ahead", prior) is True


def test_confirm_denied_without_affirmation():
    prior = [{"role": "ai", "text": "Create group 'X'? Reply yes to confirm."}]
    assert wa._confirm_allowed("create a group and add everything", prior) is False


def test_confirm_denied_without_pending_preview():
    assert wa._confirm_allowed("yes", []) is False
    assert wa._confirm_allowed("yes", [{"role": "ai", "text": "Here is your table."}]) is False


# ── _unique_sheet · openpyxl duplicate-title crash fix ────────────────────

def test_unique_sheet_dedupes_and_caps_length():
    used: set[str] = set()
    a = wa._unique_sheet("bank statement", used)
    b = wa._unique_sheet("bank statement", used)
    assert a != b
    assert all(len(t) <= 31 for t in (a, b))


def test_safe_sheet_strips_invalid_chars():
    assert "/" not in wa._safe_sheet("a/b:c?")
    assert wa._safe_sheet("") == "Sheet"


# ── _t_search_across · Hit dataclass mapping (regression) ─────────────────
# Bug: search_across used h.get(...) but retrieval.retrieve returns Hit
# dataclasses (attribute access), so EVERY content search threw → "nothing
# found". This locks the mapping to attribute access.

def test_search_across_maps_hit_dataclass_not_dict():
    from app import retrieval

    class _Doc:
        pk = 1

    orig_rows, orig_ret = wa._owner_doc_rows, retrieval.retrieve
    try:
        wa._owner_doc_rows = lambda *a, **k: [_Doc()]
        hit = retrieval.Hit(chunk_pk=5, document_pk=1, document_id_external="doc-x",
                            document_name="Invoice.pdf", page=1,
                            text="Smart Audit Pte Ltd", score=0.9,
                            bm25_rank=1, cosine_rank=None)
        retrieval.retrieve = lambda *a, **k: [hit]
        out = wa._t_search_across(None, "t", 1, query="smart audit")
    finally:
        wa._owner_doc_rows, retrieval.retrieve = orig_rows, orig_ret
    assert out["hits"][0]["document"] == "Invoice.pdf"
    assert out["hits"][0]["docId"] == "doc-x"
    assert "Smart Audit" in out["hits"][0]["text"]


# ── _wants_agent · content→RAG vs action/structured→agent routing ─────────
# Regression: routing EVERY question through the agent made content answers
# worse than the old RAG path ("bank details" returned only the bank name).

def test_wants_agent_content_questions_use_rag():
    from app.services.workspace_chat import _wants_agent
    for q in ["smart audit bank details to transfer money",
              "give me a summary about smart audit",
              "what is the total on the smart audit invoice",
              "who is the beneficiary",
              "when was the invoice dated"]:
        assert _wants_agent(q) is False, q


def test_wants_agent_action_and_structured_use_agent():
    from app.services.workspace_chat import _wants_agent
    for q in ["create a group called Tax 2026",
              "add acme to the Tax group",
              "rename acme_invoice to March Invoice",
              "tag acme as tax",
              "give me a table of all my invoices",
              "export everything to excel",
              "compare my two invoices",
              "do I have any duplicates"]:
        assert _wants_agent(q) is True, q


def test_wants_agent_bare_yes_only_with_pending_confirm():
    from app.services.workspace_chat import _wants_agent
    pending = [{"role": "ai", "text": "Create group 'X'? Reply yes to confirm."}]
    assert _wants_agent("yes please", pending) is True
    assert _wants_agent("yes please", None) is False
    assert _wants_agent("yes", [{"role": "ai", "text": "Here is your summary."}]) is False


# ── _pii_extra_terms · org/authority names are NOT redacted as people ─────

def test_pii_extra_terms_skips_orgs():
    from types import SimpleNamespace

    from app.services.workspace_chat import _pii_extra_terms
    doc = SimpleNamespace(extracted_fields={"fields": {
        "parties": [{"name": "KALYANI GODA", "role": "Applicant"},
                    {"name": "U.S. Department of Homeland Security", "role": "Issuer"}],
        "issuer_name": "Acme Pte Ltd",
        "applicant_name": "John Tan",
    }})
    persons = [v for k, v in _pii_extra_terms([doc]) if k == "person"]
    assert "KALYANI GODA" in persons
    assert "John Tan" in persons
    assert not any("Homeland" in p or "Pte Ltd" in p for p in persons)


# ── _rows_to_csv · quoting ────────────────────────────────────────────────

def test_rows_to_csv_quotes_embedded_commas():
    csv = wa._rows_to_csv(["Document", "total"],
                          [{"Document": "a.txt", "total": "USD 12,420.00"}])
    assert '"USD 12,420.00"' in csv
    assert csv.splitlines()[0] == "Document,total"
