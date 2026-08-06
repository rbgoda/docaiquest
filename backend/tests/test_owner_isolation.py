"""Per-user (owner_user_pk) isolation — the documents product's PRIMARY security
boundary, previously untested (only tenant-level isolation had coverage).

Within ONE tenant, self-registered users share a container but must never see
each other's documents. The boundary is the `current_owner_user_pk` ContextVar
(app.documents_scope) applied by `_owner_clause()` in repositories/documents.py.
These tests exercise it at the repository layer against a real Postgres+pgvector
schema (skipped when DOCAIQ_TEST_DATABASE_URL is unset, like the other DB tests).

Covers:
  · cross-owner reads return nothing (get_row / get_row_by_pk / get_by_sha256)
  · per-user counts (count_for_owner)
  · fail-closed deny sentinel (owner pk ≤ 0 → match nothing)
  · no-scope no-op (owner None → auditing behaviour, sees all)
  · group sharing: a shared doc is visible to group members, not to outsiders
"""
from __future__ import annotations

import os

import pytest

TEST_DB_URL = os.environ.get("DOCAIQ_TEST_DATABASE_URL")
T = "tenant-iso"


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


@pytest.fixture(autouse=True)
def _scope():
    """Set the tenant for every test and clear both scopes afterwards so a stray
    ContextVar never leaks into the next test."""
    from app.db import set_current_tenant
    from app.documents_scope import set_current_owner_user_pk
    set_current_tenant(T)
    set_current_owner_user_pk(None)
    yield
    set_current_owner_user_pk(None)
    set_current_tenant(None)


# ── helpers ──────────────────────────────────────────────────────────────────

def _as_owner(pk):
    from app.documents_scope import set_current_owner_user_pk
    set_current_owner_user_pk(pk)


def _mk_user(db, email):
    from app.orm import User
    u = User(tenant_id=T, email=email, name=email.split("@")[0])
    db.add(u)
    db.flush()
    return u


def _mk_doc(db, *, owner_pk, idx, sha=None, s3_key=None):
    from app.orm import Document
    d = Document(
        tenant_id=T, id_external=idx, name=f"doc {idx}", path=f"/{idx}",
        size="1", modified="2026-01-01", pages=1, current_page=1,
        type="pdf", content="pdf", ingestion_status="ready",
        owner_user_id=owner_pk, sha256=sha, s3_key=s3_key,
    )
    db.add(d)
    db.flush()
    return d


# ── tests ────────────────────────────────────────────────────────────────────

def test_owner_cannot_read_another_users_doc_by_id(db):
    from app.repositories import documents as repo
    a = _mk_user(db, "a@x.io")
    b = _mk_user(db, "b@x.io")
    _mk_doc(db, owner_pk=a.pk, idx="doc-a")
    _mk_doc(db, owner_pk=b.pk, idx="doc-b")

    _as_owner(a.pk)
    assert repo.get_row(db, "doc-a") is not None
    assert repo.get_row(db, "doc-b") is None  # B's doc invisible to A

    _as_owner(b.pk)
    assert repo.get_row(db, "doc-b") is not None
    assert repo.get_row(db, "doc-a") is None


def test_get_row_by_pk_blocks_cross_owner(db):
    from app.repositories import documents as repo
    a = _mk_user(db, "a@x.io")
    b = _mk_user(db, "b@x.io")
    doc_a = _mk_doc(db, owner_pk=a.pk, idx="doc-a")

    _as_owner(b.pk)
    # B knows/forges A's primary key — must still get nothing.
    assert repo.get_row_by_pk(db, doc_a.pk) is None
    _as_owner(a.pk)
    assert repo.get_row_by_pk(db, doc_a.pk) is not None


def test_get_by_sha256_is_owner_scoped(db):
    from app.repositories import documents as repo
    a = _mk_user(db, "a@x.io")
    b = _mk_user(db, "b@x.io")
    # Same file uploaded by both users — dedup is per-user, so each owns a copy.
    _mk_doc(db, owner_pk=a.pk, idx="doc-a", sha="deadbeef")
    _mk_doc(db, owner_pk=b.pk, idx="doc-b", sha="deadbeef")

    _as_owner(a.pk)
    hit = repo.get_by_sha256(db, "deadbeef")
    assert hit is not None and hit.owner_user_id == a.pk  # never B's row

    _as_owner(b.pk)
    hit = repo.get_by_sha256(db, "deadbeef")
    assert hit is not None and hit.owner_user_id == b.pk


def test_count_for_owner_counts_only_own(db):
    from app.repositories import documents as repo
    a = _mk_user(db, "a@x.io")
    b = _mk_user(db, "b@x.io")
    _mk_doc(db, owner_pk=a.pk, idx="a1")
    _mk_doc(db, owner_pk=a.pk, idx="a2")
    _mk_doc(db, owner_pk=b.pk, idx="b1")

    _as_owner(a.pk)
    assert repo.count_for_owner(db) == 2
    _as_owner(b.pk)
    assert repo.count_for_owner(db) == 1


def test_deny_sentinel_matches_nothing(db):
    """Authenticated but no valid owner (pk ≤ 0) must fail CLOSED — see nothing,
    not everything."""
    from app.repositories import documents as repo
    a = _mk_user(db, "a@x.io")
    _mk_doc(db, owner_pk=a.pk, idx="doc-a", s3_key="k")

    _as_owner(0)
    assert repo.get_row(db, "doc-a") is None
    assert repo.count_for_owner(db) == 0
    assert repo.list_for_backup(db) == []

    _as_owner(-5)
    assert repo.count_for_owner(db) == 0


def test_no_owner_scope_sees_all(db):
    """owner None = the auditing product / worker context: no per-user filter,
    so every row in the tenant is visible (proves the scope is opt-in)."""
    from app.repositories import documents as repo
    a = _mk_user(db, "a@x.io")
    b = _mk_user(db, "b@x.io")
    _mk_doc(db, owner_pk=a.pk, idx="doc-a")
    _mk_doc(db, owner_pk=b.pk, idx="doc-b")

    _as_owner(None)
    assert repo.get_row(db, "doc-a") is not None
    assert repo.get_row(db, "doc-b") is not None
    assert repo.count_for_owner(db) == 2


def test_group_shared_doc_visible_to_member_not_outsider(db):
    from app.orm import DocumentGroup, DocumentGroupMember, DocumentGroupShare
    from app.repositories import documents as repo
    a = _mk_user(db, "a@x.io")   # owner/sharer
    b = _mk_user(db, "b@x.io")   # group member
    c = _mk_user(db, "c@x.io")   # outsider
    doc = _mk_doc(db, owner_pk=a.pk, idx="shared")

    g = DocumentGroup(tenant_id=T, name="G", created_by_user_id=a.pk)
    db.add(g)
    db.flush()
    db.add(DocumentGroupMember(tenant_id=T, group_id=g.pk, user_id=b.pk, member_email=b.email))
    db.add(DocumentGroupShare(tenant_id=T, document_pk=doc.pk, group_id=g.pk))
    db.flush()

    _as_owner(b.pk)
    assert repo.get_row(db, "shared") is not None  # member sees the shared doc
    assert repo.count_for_owner(db) == 1

    _as_owner(c.pk)
    assert repo.get_row(db, "shared") is None       # outsider does not
    assert repo.count_for_owner(db) == 0
