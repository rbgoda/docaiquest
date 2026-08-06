"""M44.P10 PR2 · tests for the delete-with-learning promotion engine.

Two layers:
  · Pure-logic tests (no DB) — the anonymizer, identifier gate, template
    derivation, and trace-success predicate. Always run in CI.
  · DB integration test — exercises promote_doc_learnings end-to-end against
    a real Postgres+pgvector. Auto-skips when DOCAIQ_TEST_DATABASE_URL is
    unset or unreachable, so DB-less CI stays green. Run locally via:

        DOCAIQ_TEST_DATABASE_URL=postgresql+psycopg://docaiq:pw@localhost:5544/docaiq \
          pytest tests/test_learning_promoter.py -v
"""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from app.services import learning_promoter as lp


# ── Pure logic · identifier gate ──────────────────────────────────────────

@pytest.mark.parametrize(
    "question",
    [
        "What is the policy number 4421887?",        # long digit run
        "Email john@acme.com about renewal",          # email
        "Is the premium $1,250 paid?",                 # money
        "Was it issued on 2024-03-01?",                # iso date
        "Does it expire 01/03/2025?",                  # dd/mm/yyyy
        "Is control AC-2 satisfied?",                  # control id
        "Who is the signatory, Rajesh Goda?",          # proper noun
    ],
)
def test_identifier_gate_blocks_doc_specific(question):
    assert lp.has_doc_specific_identifier(question) is True


@pytest.mark.parametrize(
    "question",
    [
        "What is the expiry date of this certificate?",
        "Does the policy cover third-party liability?",
        "Is there an issuing authority listed?",
        "What type of insurance is this?",
        "",
    ],
)
def test_identifier_gate_allows_generic(question):
    assert lp.has_doc_specific_identifier(question) is False


# ── Pure logic · anonymizer ───────────────────────────────────────────────

def test_anonymize_replaces_identifiers():
    out = lp.anonymize_question("Policy 4421887 emailed to a@b.com for $1,250 on 2024-03-01")
    assert "4421887" not in out
    assert "a@b.com" not in out
    assert "1,250" not in out
    assert "2024-03-01" not in out
    assert "{number}" in out and "{email}" in out and "{amount}" in out and "{date}" in out


def test_anonymize_noop_on_generic():
    q = "What is the expiry date of this certificate?"
    assert lp.anonymize_question(q) == q


def test_question_template_is_lowercased_anonymized():
    assert lp.question_template("What is the Policy 999999?") == "what is the policy {number}?"


# ── Pure logic · trace success predicate ──────────────────────────────────

def _step(action_name, error=None):
    return SimpleNamespace(action_name=action_name, error=error)


def test_successful_trace_terminates_in_final_answer():
    steps = [_step("search_chunks"), _step("get_extracted_field"), _step("final_answer")]
    assert lp.is_successful_trace(steps) is True
    assert lp.tool_sequence(steps) == ["search_chunks", "get_extracted_field", "final_answer"]


def test_trace_with_error_is_not_successful():
    steps = [_step("search_chunks", error="tool blew up"), _step("final_answer")]
    assert lp.is_successful_trace(steps) is False


def test_trace_not_terminating_in_final_answer_is_not_successful():
    assert lp.is_successful_trace([_step("search_chunks")]) is False
    assert lp.is_successful_trace([]) is False


# ── DB integration · full Phase-1 promotion ───────────────────────────────

TEST_DB_URL = os.environ.get("DOCAIQ_TEST_DATABASE_URL")


@pytest.fixture()
def db():
    """A clean schema on a real Postgres+pgvector. Skips if no test DB."""
    if not TEST_DB_URL:
        pytest.skip("DOCAIQ_TEST_DATABASE_URL not set — DB integration test skipped")
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(TEST_DB_URL)
    try:
        with engine.connect() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            conn.commit()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"test DB unreachable: {e}")

    from app.db import Base
    import app.orm  # noqa: F401 — register all tables on Base.metadata

    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    session = Session()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


T = "tenant-x"


def _doc(db, *, ext, doc_type, pk_hint=None):
    from app.orm import Document
    d = Document(
        tenant_id=T, id_external=ext, name=f"{ext}.pdf", path="/x", size="1kb",
        modified="now", pages=1, current_page=1, type="pdf", content="paper",
        doc_type=doc_type,
    )
    db.add(d)
    db.flush()
    return d


def test_promote_reflexions_only_helpful_and_generic(db):
    from app.orm import ReflexionPair
    doc = _doc(db, ext="doc-1", doc_type="insurance_certificate")
    # qualifies: helpful, generic
    db.add(ReflexionPair(tenant_id=T, question="What is the expiry date of this policy?",
                         draft_answer="d", final_answer="2025", doc_id_external="doc-1",
                         helpful_count=3, marked_unhelpful_count=0, kind="doc_specific"))
    # blocked: references a specific number
    db.add(ReflexionPair(tenant_id=T, question="Is policy 998877 active?",
                         draft_answer="d", final_answer="yes", doc_id_external="doc-1",
                         helpful_count=5, marked_unhelpful_count=0, kind="doc_specific"))
    # blocked: not helpful enough
    db.add(ReflexionPair(tenant_id=T, question="What kind of coverage is included?",
                         draft_answer="d", final_answer="x", doc_id_external="doc-1",
                         helpful_count=1, marked_unhelpful_count=0, kind="doc_specific"))
    # blocked: contested
    db.add(ReflexionPair(tenant_id=T, question="Does it cover flood damage?",
                         draft_answer="d", final_answer="x", doc_id_external="doc-1",
                         helpful_count=2, marked_unhelpful_count=2, kind="doc_specific"))
    db.flush()

    summary = lp.promote_doc_learnings(db, doc.pk)
    db.commit()

    assert summary["reflexions_promoted"] == 1
    general = db.query(ReflexionPair).filter(ReflexionPair.kind == "general").all()
    assert len(general) == 1
    assert general[0].doc_id_external is None
    # the 3 non-qualifying rows stay doc_specific (Phase 2 will cascade them)
    assert db.query(ReflexionPair).filter(ReflexionPair.kind == "doc_specific").count() == 3
    # doc was marked pending
    assert doc.deletion_status == "pending"


def test_promote_field_edits_needs_threshold_docs(db):
    from app.orm import FieldEdit, ExtractionCorrection
    docs = [_doc(db, ext=f"d{i}", doc_type="bank_statement") for i in range(3)]
    # same field corrected on all 3 docs of this type → qualifies
    for d in docs:
        db.add(FieldEdit(tenant_id=T, document_pk=d.pk, field_path="fields.balance",
                         original_value="0", new_value="100", edited_by="u@x.io"))
    # a field corrected on only 1 doc → does NOT qualify
    db.add(FieldEdit(tenant_id=T, document_pk=docs[0].pk, field_path="fields.iban",
                     original_value="a", new_value="b", edited_by="u@x.io"))
    db.flush()

    summary = lp.promote_doc_learnings(db, docs[0].pk)
    db.commit()

    assert summary["corrections_upserted"] == 1
    rows = db.query(ExtractionCorrection).all()
    assert len(rows) == 1
    assert rows[0].pattern["wrong_field"] == "fields.balance"
    assert rows[0].observations_count == 3
    assert rows[0].source == "local"


def test_promote_agent_skills_only_successful_traces(db):
    from app.orm import ChatMessage, AgentTrace, AgentSkillMemory
    doc = _doc(db, ext="doc-k", doc_type="kyc_passport")
    # user question + AI answer with a SUCCESSFUL trace
    db.add(ChatMessage(tenant_id=T, doc_id_external="doc-k", role="user",
                       text="What is the passport number 12345?"))
    db.flush()
    ai = ChatMessage(tenant_id=T, doc_id_external="doc-k", role="ai", text="It is X")
    db.add(ai)
    db.flush()
    for i, (name, err) in enumerate([("search_chunks", None), ("get_extracted_field", None), ("final_answer", None)]):
        db.add(AgentTrace(tenant_id=T, chat_message_pk=ai.pk, step_index=i, action_name=name, error=err))
    # a second AI answer whose trace ERRORED → must be ignored
    db.add(ChatMessage(tenant_id=T, doc_id_external="doc-k", role="user", text="And the expiry?"))
    db.flush()
    ai2 = ChatMessage(tenant_id=T, doc_id_external="doc-k", role="ai", text="?")
    db.add(ai2)
    db.flush()
    db.add(AgentTrace(tenant_id=T, chat_message_pk=ai2.pk, step_index=0, action_name="search_chunks", error="boom"))
    db.flush()

    summary = lp.promote_doc_learnings(db, doc.pk)
    db.commit()

    assert summary["skills_upserted"] == 1
    skills = db.query(AgentSkillMemory).all()
    assert len(skills) == 1
    # question template was anonymized (no "12345")
    assert "12345" not in skills[0].question_template
    assert skills[0].tool_sequence == ["search_chunks", "get_extracted_field", "final_answer"]


def test_promote_entities_merges_aliases(db):
    from app.orm import Entity, EntityCanonical
    doc = _doc(db, ext="doc-e", doc_type="contract")
    db.add(Entity(tenant_id=T, document_pk=doc.pk, kind="org", text="Acme Corp.",
                  canonical="Acme Corporation", page=1, source="fact_bootstrap"))
    db.add(Entity(tenant_id=T, document_pk=doc.pk, kind="org", text="ACME",
                  canonical="Acme Corporation", page=1, source="fact_bootstrap"))
    # a money entity must be ignored (only person/org promote)
    db.add(Entity(tenant_id=T, document_pk=doc.pk, kind="money", text="$5", page=1, source="regex"))
    db.flush()

    summary = lp.promote_doc_learnings(db, doc.pk)
    db.commit()

    assert summary["canonicals_upserted"] == 1
    rows = db.query(EntityCanonical).all()
    assert len(rows) == 1
    assert rows[0].canonical == "Acme Corporation"
    assert set(rows[0].aliases) == {"Acme Corp.", "ACME"}


def test_promotion_is_idempotent_on_corrections(db):
    """Running twice must not duplicate a correction (functional unique
    index dedup) — observations_count just refreshes."""
    from app.orm import FieldEdit, ExtractionCorrection
    docs = [_doc(db, ext=f"c{i}", doc_type="invoice") for i in range(3)]
    for d in docs:
        db.add(FieldEdit(tenant_id=T, document_pk=d.pk, field_path="fields.total",
                         original_value="0", new_value="9", edited_by="u@x.io"))
    db.flush()

    lp.promote_doc_learnings(db, docs[0].pk)
    lp.promote_doc_learnings(db, docs[1].pk)
    db.commit()

    assert db.query(ExtractionCorrection).count() == 1


def test_lock_select_for_update_does_not_error(db):
    """promote_doc_learnings(lock=True) must run the SELECT ... FOR UPDATE
    path without error (PR3 calls it with lock=True inside the request txn)."""
    doc = _doc(db, ext="doc-lock", doc_type="invoice")
    summary = lp.promote_doc_learnings(db, doc.pk, lock=True)
    db.commit()
    assert summary["doc_pk"] == doc.pk
    assert doc.deletion_status == "pending"


# ── PR3 · two-phase delete: Phase 1 promote → Phase 2 cascade ─────────────

def test_two_phase_delete_keeps_promoted_purges_doc_specific(db):
    """The integration that PR3 wires into the DELETE endpoint: promote, then
    delete_row. Promoted (general) reflexion pairs survive; un-promoted
    doc-specific ones are purged with the document."""
    from app.db import set_current_tenant, set_current_vendor_pk
    from app.orm import ReflexionPair, Document
    from app.repositories import documents as docrepo

    set_current_tenant(T)
    set_current_vendor_pk(None)
    try:
        doc = _doc(db, ext="doc-tp", doc_type="insurance_certificate")
        # promotable: helpful + generic
        db.add(ReflexionPair(tenant_id=T, question="What is the coverage limit type?",
                             draft_answer="d", final_answer="x", doc_id_external="doc-tp",
                             helpful_count=3, marked_unhelpful_count=0, kind="doc_specific"))
        # NOT promotable: references a specific number → stays doc_specific
        db.add(ReflexionPair(tenant_id=T, question="Is policy 778899 active?",
                             draft_answer="d", final_answer="x", doc_id_external="doc-tp",
                             helpful_count=9, marked_unhelpful_count=0, kind="doc_specific"))
        db.flush()

        # Phase 1
        summary = lp.promote_doc_learnings(db, doc.pk, lock=True)
        assert summary["reflexions_promoted"] == 1
        # Phase 2
        deleted = docrepo.delete_row(db, "doc-tp")
        db.commit()

        assert deleted is not None
        # document is gone
        assert db.query(Document).filter(Document.id_external == "doc-tp").count() == 0
        # exactly one reflexion pair survives — the promoted general one
        survivors = db.query(ReflexionPair).all()
        assert len(survivors) == 1
        assert survivors[0].kind == "general"
        assert survivors[0].doc_id_external is None
        # the doc-specific (un-promoted) pair was purged with the doc
        assert db.query(ReflexionPair).filter(
            ReflexionPair.doc_id_external == "doc-tp"
        ).count() == 0
    finally:
        set_current_tenant(None)
        set_current_vendor_pk(None)
