"""workspace_chat.clear_thread — the chat 'Reset' action.

Verifies the reset wipes only the signed-in user's own thread (per-user isolation:
the thread anchor is `user:<owner_pk>`), and never another user's conversation.
DB-backed; skips when DOCAIQ_TEST_DATABASE_URL is unset like the other DB tests."""
from __future__ import annotations

import os

import pytest

TEST_DB_URL = os.environ.get("DOCAIQ_TEST_DATABASE_URL")
T = "tenant-reset"


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
    import app.orm  # noqa: F401 — register tables on Base.metadata

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
    from app.db import set_current_tenant
    from app.documents_scope import set_current_owner_user_pk
    set_current_tenant(T)
    set_current_owner_user_pk(None)
    yield
    set_current_owner_user_pk(None)
    set_current_tenant(None)


def _as_owner(pk):
    from app.documents_scope import set_current_owner_user_pk
    set_current_owner_user_pk(pk)


def _post(db, text):
    """Persist a user message on the CURRENT owner's workspace thread."""
    from app.orm import ChatMessage
    from app.services.workspace_chat import workspace_key
    m = ChatMessage(tenant_id=T, workspace_key=workspace_key(None), role="user", text=text)
    db.add(m)
    db.flush()
    return m


def _thread_len(db, owner_pk):
    from app.orm import ChatMessage
    from app.services.workspace_chat import workspace_key
    _as_owner(owner_pk)
    wkey = workspace_key(None)
    from sqlalchemy import func, select
    return db.scalar(select(func.count()).select_from(ChatMessage)
                     .where(ChatMessage.tenant_id == T, ChatMessage.workspace_key == wkey))


def test_clear_thread_wipes_own_messages(db):
    from app.services.workspace_chat import clear_thread
    _as_owner(11)
    _post(db, "hello")
    _post(db, "and another")
    db.commit()
    assert _thread_len(db, 11) == 2

    _as_owner(11)
    removed = clear_thread(db, T, None)
    assert removed == 2
    assert _thread_len(db, 11) == 0


def test_clear_thread_is_owner_scoped(db):
    """Owner A's reset must not touch owner B's thread (anchor = user:<pk>)."""
    from app.services.workspace_chat import clear_thread
    _as_owner(1)
    _post(db, "A-1")
    _post(db, "A-2")
    _as_owner(2)
    _post(db, "B-1")
    db.commit()

    _as_owner(1)
    removed = clear_thread(db, T, None)
    assert removed == 2                 # only A's two
    assert _thread_len(db, 1) == 0      # A wiped
    assert _thread_len(db, 2) == 1      # B untouched


def test_clear_empty_thread_is_zero(db):
    from app.services.workspace_chat import clear_thread
    _as_owner(99)
    assert clear_thread(db, T, None) == 0
