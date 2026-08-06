"""workspace_handlers._answer_entity_lookup — the generic entity + type resolver: resolve a
named graph entity (any kind) → its documents → optional doc-type filter → values + source.
DB integration (real pgvector) when DOCAIQ_TEST_DATABASE_URL is set."""
from __future__ import annotations

import os

import pytest

TEST_DB_URL = os.environ.get("DOCAIQ_TEST_DATABASE_URL")
T = "tenant-entres"


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
    from app.db import Base, set_current_tenant
    from app.documents_scope import set_current_owner_user_pk
    import app.orm  # noqa: F401
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    s = Session()
    set_current_tenant(T)
    # owner_user_id on documents is an FK → users; create the owner (pk=1).
    from app.orm import User
    s.add(User(pk=1, tenant_id=T, email="owner@test", name="Owner"))
    s.flush()
    try:
        yield s
    finally:
        s.rollback()
        s.close()
        set_current_owner_user_pk(None)  # don't leak owner scope into the next test
        set_current_tenant(None)
        Base.metadata.drop_all(engine)
        engine.dispose()


def _mk(db, *, id_external, doc_type, fields):
    from app.orm import Document
    d = Document(
        tenant_id=T, id_external=id_external, name=f"{doc_type}-{id_external}", path=f"/{id_external}",
        size="1", modified="2026-01-01", pages=1, current_page=1, type="pdf", content="pdf",
        ingestion_status="ready", owner_user_id=1, sha256=id_external, s3_key=id_external,
        doc_type=doc_type, extracted_fields={"fields": fields})
    db.add(d)
    db.flush()
    return d


def _ent(db, *, doc_pk, kind, canonical, txt):
    from app.orm import Entity
    db.add(Entity(tenant_id=T, document_pk=doc_pk, kind=kind, text=txt, canonical=canonical, page=1))


def _setup(db, monkeypatch):
    from app.config import get_settings
    from app.documents_scope import set_current_owner_user_pk
    monkeypatch.setattr(get_settings(), "entity_type_resolver", True)
    set_current_owner_user_pk(1)
    nid = _mk(db, id_external="nid1", doc_type="national_id",
              fields={"full_name": "Rajesh Goda", "id_number": "S123",
                      "nationality": "Singapore"})
    inv = _mk(db, id_external="inv1", doc_type="invoice", fields={"total": "99"})
    _ent(db, doc_pk=nid.pk, kind="person", canonical="goda rajesh balvantrai", txt="Mr Goda Rajesh Balvantrai")
    _ent(db, doc_pk=inv.pk, kind="org", canonical="acme corp", txt="Acme Corp")
    db.commit()


def test_resolves_person_and_narrows_to_national_id(db, monkeypatch):
    from app.services.workspace_handlers import _answer_entity_lookup
    _setup(db, monkeypatch)
    # 'Rajesh Goda' (reordered/partial) must match the canonical 'goda rajesh balvantrai'
    out = _answer_entity_lookup(db, T, "show me Rajesh Goda's national id")
    assert out is not None
    assert "national_id-nid1" in out
    assert "S123" in out  # the ID number is surfaced
    assert "invoice" not in out.lower()  # narrowed to the national_id type only


def test_resolves_org_with_type(db, monkeypatch):
    from app.services.workspace_handlers import _answer_entity_lookup
    _setup(db, monkeypatch)
    out = _answer_entity_lookup(db, T, "invoices from Acme Corp")
    assert out is not None and "invoice-inv1" in out


def test_unknown_entity_returns_none(db, monkeypatch):
    from app.services.workspace_handlers import _answer_entity_lookup
    _setup(db, monkeypatch)
    assert _answer_entity_lookup(db, T, "show me Napoleon's passport") is None


def test_flag_off_returns_none(db, monkeypatch):
    from app.config import get_settings
    from app.documents_scope import set_current_owner_user_pk
    from app.services.workspace_handlers import _answer_entity_lookup
    _setup(db, monkeypatch)
    monkeypatch.setattr(get_settings(), "entity_type_resolver", False)
    set_current_owner_user_pk(1)
    assert _answer_entity_lookup(db, T, "show me Rajesh Goda's national id") is None
