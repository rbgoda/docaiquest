"""Account-erase completeness + isolation (GDPR Art. 17 "delete everything").

Verifies the purge now reaches what DB cascade misses — object-storage blobs,
llm_call_audit, llm_calls, group-thread chat — WITHOUT touching another user's data.
"""
from __future__ import annotations

import os

import pytest

TEST_DB_URL = os.environ.get("DOCAIQ_TEST_DATABASE_URL")
T = "tenant-erase"


@pytest.fixture()
def db():
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
    import app.orm  # noqa: F401
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    s = Session()
    try:
        yield s
    finally:
        s.rollback()
        s.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.fixture(autouse=True)
def _scope():
    from app.db import set_current_tenant
    from app.documents_scope import set_current_owner_user_pk
    set_current_tenant(T)
    set_current_owner_user_pk(None)
    yield
    set_current_owner_user_pk(None)
    set_current_tenant(None)


def _mk_user(db, email):
    from app.orm import User
    u = User(tenant_id=T, email=email, name=email.split("@")[0])
    db.add(u)
    db.flush()
    return u


def _mk_doc(db, owner_pk, idx, s3_key):
    from app.orm import Document
    d = Document(tenant_id=T, id_external=idx, name=f"doc {idx}", path=f"/{idx}",
                 size="1", modified="2026-01-01", pages=1, current_page=1,
                 type="pdf", content="pdf", ingestion_status="ready",
                 owner_user_id=owner_pk, doc_type="invoice", s3_key=s3_key)
    db.add(d)
    db.flush()
    return d


def _audit(db, email):
    from app.orm import LLMCallAudit
    db.add(LLMCallAudit(tenant_id=T, provider="p", model="m", prompt_sha256="h", user_email=email))


def test_erase_completeness_and_isolation(db, monkeypatch):
    from app.documents_scope import set_current_owner_user_pk
    from app.orm import ChatMessage, Document, DocumentGroup, LLMCall, LLMCallAudit
    from app.services import data_rights

    deleted_keys = []
    monkeypatch.setattr("app.storage.delete_object", lambda k: deleted_keys.append(k))

    b = _mk_user(db, "b@x.io")
    a = _mk_user(db, "a@x.io")
    d1 = _mk_doc(db, b.pk, "b-1", "s3/b-1")
    _mk_doc(db, b.pk, "b-2", "s3/b-2")
    a1 = _mk_doc(db, a.pk, "a-1", "s3/a-1")
    _audit(db, "b@x.io")
    _audit(db, "a@x.io")                       # A's — must survive
    db.add(LLMCall(tenant_id=T, task_type="chat", tier="1", provider="p",
                   model="m", status="ok", document_pk=d1.pk))
    g = DocumentGroup(tenant_id=T, name="G", created_by_user_id=b.pk)
    db.add(g)
    db.flush()
    db.add(ChatMessage(tenant_id=T, workspace_key=f"group:{g.pk}", role="user", text="hi"))
    db.commit()

    set_current_owner_user_pk(b.pk)
    counts = data_rights.erase_user_data(db, uid=b.pk, email="b@x.io", tenant_id=T)

    # B fully erased ...
    assert db.query(Document).filter(Document.owner_user_id == b.pk).count() == 0
    assert counts["documents"] == 2
    assert counts["storageBlobs"] == 2 and set(deleted_keys) == {"s3/b-1", "s3/b-2"}
    assert counts["llmAudit"] == 1
    assert counts["llmCalls"] == 1
    assert counts["groupChat"] == 1
    assert db.query(LLMCall).count() == 0
    assert db.query(ChatMessage).filter(ChatMessage.workspace_key == f"group:{g.pk}").count() == 0

    # ... A untouched.
    assert db.get(Document, a1.pk) is not None
    assert db.query(LLMCallAudit).filter(LLMCallAudit.user_email == "a@x.io").count() == 1
    assert "s3/a-1" not in deleted_keys


def _entity(db, doc, canonical, kind="org"):
    from app.orm import Entity
    db.add(Entity(tenant_id=T, document_pk=doc.pk, kind=kind, text=canonical.title(),
                  canonical=canonical, page=1))


def _canon(db, canonical, kind="org"):
    from app.orm import EntityCanonical
    db.add(EntityCanonical(tenant_id=T, kind=kind, canonical=canonical))


def test_erase_deletes_only_solely_referenced_canonicals(db, monkeypatch):
    """A canonical name referenced ONLY by the erased user is removed; one another
    user's doc also references must survive (tenant-global table, no over-delete)."""
    from app.documents_scope import set_current_owner_user_pk
    from app.orm import EntityCanonical
    from app.services import data_rights
    monkeypatch.setattr("app.storage.delete_object", lambda k: None)

    b = _mk_user(db, "b@x.io")
    a = _mk_user(db, "a@x.io")
    db_b = _mk_doc(db, b.pk, "b-1", None)
    db_a = _mk_doc(db, a.pk, "a-1", None)
    _entity(db, db_b, "acme only b")     # solely B
    _entity(db, db_b, "shared corp")     # B ...
    _entity(db, db_a, "shared corp")     # ... and A → must survive
    _canon(db, "acme only b")
    _canon(db, "shared corp")
    db.commit()

    set_current_owner_user_pk(b.pk)
    counts = data_rights.erase_user_data(db, uid=b.pk, email="b@x.io", tenant_id=T)

    assert counts["entityCanonical"] == 1
    remaining = {c.canonical for c in db.query(EntityCanonical).all()}
    assert remaining == {"shared corp"}   # solely-B name gone, shared name kept


def test_export_detokenizes_owner_pii(db, monkeypatch):
    """DSAR export returns the user's REAL values, not [TOKEN] placeholders."""
    from app.documents_scope import set_current_owner_user_pk
    from app.services import data_rights

    # Stub the retrieval of chunks + the vault detokenize so the test is hermetic.
    class _Chunk:
        text = "card [CREDIT_CARD_1] on file"
    monkeypatch.setattr("app.repositories.documents.chunks_for_doc",
                        lambda db, pk, tenant_id: [_Chunk()])
    monkeypatch.setattr("app.pii_vault.detokenize",
                        lambda db, pk, txt: txt.replace("[CREDIT_CARD_1]", "4111-1111-1111-1111"))

    u = _mk_user(db, "e@x.io")
    from app.orm import Document
    d = Document(tenant_id=T, id_external="e-1", name="d", path="/e", size="1",
                 modified="2026-01-01", pages=1, current_page=1, type="pdf", content="pdf",
                 ingestion_status="ready", owner_user_id=u.pk, doc_type="invoice",
                 pii_protected=True)
    db.add(d)
    db.commit()
    set_current_owner_user_pk(u.pk)
    out = data_rights.export_user_data(db, uid=u.pk, email="e@x.io", tenant_id=T)
    assert "4111-1111-1111-1111" in out["documents"][0]["text"]
    assert "[CREDIT_CARD_1]" not in out["documents"][0]["text"]
