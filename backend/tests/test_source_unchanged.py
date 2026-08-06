"""Drive autosync change-detection — repo.source_unchanged lets the sync skip
re-downloading a connector file whose modifiedTime is unchanged."""
from __future__ import annotations

import os

import pytest

TEST_DB_URL = os.environ.get("DOCAIQ_TEST_DATABASE_URL")
T = "tenant-sync"


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
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
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


def _mk_doc(db, owner_pk, idx, source_ref, modified):
    from app.orm import Document
    d = Document(tenant_id=T, id_external=idx, name=idx, path="/x", size="1",
                 modified=modified, pages=1, current_page=1, type="pdf", content="pdf",
                 ingestion_status="ready", owner_user_id=owner_pk,
                 source="drive", source_ref=source_ref)
    db.add(d)
    db.flush()
    return d


def _as_owner(pk):
    from app.documents_scope import set_current_owner_user_pk
    set_current_owner_user_pk(pk)


def test_unchanged_when_modified_matches(db):
    from app.repositories import documents as repo
    a = _mk_user(db, "a@x.io")
    _as_owner(a.pk)
    _mk_doc(db, a.pk, "doc-a", "drive-file-1", "2026-06-01T00:00:00Z")
    assert repo.source_unchanged(db, "drive-file-1", "2026-06-01T00:00:00Z") is True


def test_changed_when_modified_differs(db):
    from app.repositories import documents as repo
    a = _mk_user(db, "a@x.io")
    _as_owner(a.pk)
    _mk_doc(db, a.pk, "doc-a", "drive-file-1", "2026-06-01T00:00:00Z")
    assert repo.source_unchanged(db, "drive-file-1", "2026-06-02T00:00:00Z") is False


def test_false_when_no_modified_or_no_ref(db):
    from app.repositories import documents as repo
    assert repo.source_unchanged(db, "drive-file-1", None) is False
    assert repo.source_unchanged(db, "", "2026-06-01T00:00:00Z") is False


def test_owner_scoped(db):
    """Another owner's identical Drive file must NOT count as 'unchanged' for me."""
    from app.repositories import documents as repo
    a = _mk_user(db, "a@x.io")
    b = _mk_user(db, "b@x.io")
    _as_owner(a.pk)
    _mk_doc(db, a.pk, "doc-a", "drive-file-1", "2026-06-01T00:00:00Z")
    _as_owner(b.pk)
    assert repo.source_unchanged(db, "drive-file-1", "2026-06-01T00:00:00Z") is False
