"""Round-4 hardening: agentic + extraction security/robustness fixes.

  · related_docs now scopes to the CALLER, not the doc owner — opening a group-shared
    doc must not enumerate the doc owner's OTHER private documents.
  · _ics_escape handles bare CR (iCal line injection).
"""
from __future__ import annotations

import os

import pytest

TEST_DB_URL = os.environ.get("DOCAIQ_TEST_DATABASE_URL")
T = "tenant-r4"


# ── _ics_escape (pure) ────────────────────────────────────────────────────────
def test_ics_escape_handles_cr():
    from app.routers.assistant import _ics_escape
    assert "\r" not in _ics_escape("line1\r\nline2")
    assert "\r" not in _ics_escape("bare\rcr")
    assert _ics_escape("a\r\nb") == "a\\nb"
    assert _ics_escape("x; y, z") == "x\\; y\\, z"


# ── DB fixture ────────────────────────────────────────────────────────────────
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


def _mk_doc(db, owner_pk, idx, doc_type="invoice", fields=None):
    from app.orm import Document
    d = Document(tenant_id=T, id_external=idx, name=f"doc {idx}", path=f"/{idx}",
                 size="1", modified="2026-01-01", pages=1, current_page=1,
                 type="pdf", content="pdf", ingestion_status="ready",
                 owner_user_id=owner_pk, doc_type=doc_type,
                 extracted_fields={"fields": fields or {}})
    db.add(d)
    db.flush()
    return d


def _mk_entity(db, doc, canonical, kind="org"):
    from app.orm import Entity
    e = Entity(tenant_id=T, document_pk=doc.pk, kind=kind, text=canonical.title(),
               canonical=canonical, page=1)
    db.add(e)
    db.flush()
    return e


def test_related_docs_scopes_to_caller_not_doc_owner(db):
    """B owns two docs sharing an org entity. B sees the sibling; a DIFFERENT caller
    (A, who only has the one doc group-shared) must NOT see B's other private doc."""
    from app.documents_scope import set_current_owner_user_pk
    from app.services.related_docs import find_related
    b = _mk_user(db, "b@x.io")
    a = _mk_user(db, "a@x.io")
    d1 = _mk_doc(db, b.pk, "b-1")
    d2 = _mk_doc(db, b.pk, "b-2")
    _mk_entity(db, d1, "acme corporation")
    _mk_entity(db, d2, "acme corporation")
    db.commit()

    # Caller = B (the owner): sees the related sibling.
    set_current_owner_user_pk(b.pk)
    rel_b = find_related(db, d1)
    assert any(r["id"] == "b-2" for r in rel_b), "owner should see their own related doc"

    # Caller = A (opening B's shared doc): must NOT enumerate B's other private doc.
    set_current_owner_user_pk(a.pk)
    rel_a = find_related(db, d1)
    assert rel_a == [], f"cross-user leak: A saw {rel_a}"


