"""Session revocation — token_version stamped into the JWT + checked per request
when the flag is on. Flag off / no-tv token = today's pure-JWT path (no DB, valid)."""
from __future__ import annotations

import os

import jwt
import pytest

TEST_DB_URL = os.environ.get("DOCAIQ_TEST_DATABASE_URL")


def _tok(uid, tv, tenant, *, with_tv=True):
    from app.auth import issue_session_token
    t = issue_session_token(user_id=uid, email="u@x.io", name="U",
                            org_id=tenant, roles=[], token_version=tv)
    if with_tv:
        return t
    # craft an OLD-style token (no `tv` claim) to prove pre-feature sessions are exempt
    from app.config import get_settings
    claims = jwt.decode(t, get_settings().jwt_secret, algorithms=["HS256"], issuer="docaiq",
                        options={"verify_exp": False})
    claims.pop("tv", None)
    return jwt.encode(claims, get_settings().jwt_secret, algorithm="HS256")


def test_issue_token_carries_tv():
    from app.config import get_settings
    tok = _tok(1, 5, get_settings().tenant_id)
    claims = jwt.decode(tok, get_settings().jwt_secret, algorithms=["HS256"], issuer="docaiq")
    assert claims["tv"] == 5


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


def _set_flag(v):
    from app.config import get_settings
    object.__setattr__(get_settings(), "session_revocation", v)


def test_revocation_enforced_only_when_on(db):
    from fastapi import HTTPException

    from app.config import get_settings
    from app.orm import User
    from app.security import _make_current_user_dependency
    get_current_user = _make_current_user_dependency()
    tenant = get_settings().tenant_id
    u = User(tenant_id=tenant, email="u@x.io", name="U", token_version=2)
    db.add(u)
    db.commit()
    try:
        # Flag OFF → a stale token (tv=0 < db.token_version=2) is still accepted (no DB check).
        _set_flag(False)
        assert get_current_user(session_cookie=_tok(u.pk, 0, tenant)).id == u.pk

        # Flag ON → stale token rejected, current token accepted.
        _set_flag(True)
        with pytest.raises(HTTPException) as ei:
            get_current_user(session_cookie=_tok(u.pk, 0, tenant))
        assert ei.value.status_code == 401
        assert get_current_user(session_cookie=_tok(u.pk, 2, tenant)).id == u.pk

        # Flag ON but token has NO `tv` claim (pre-feature session) → exempt, accepted.
        assert get_current_user(session_cookie=_tok(u.pk, 0, tenant, with_tv=False)).id == u.pk

        # Flag ON + account frozen → rejected even with a current token.
        u.is_frozen = True
        db.commit()
        with pytest.raises(HTTPException) as ei2:
            get_current_user(session_cookie=_tok(u.pk, 2, tenant))
        assert ei2.value.status_code == 401
    finally:
        _set_flag(False)


def test_bumping_token_version_revokes(db):
    """The mechanism logout-all / password-change use: bump → old token stale."""
    from app.config import get_settings
    from app.orm import User
    from app.security import _make_current_user_dependency
    get_current_user = _make_current_user_dependency()
    tenant = get_settings().tenant_id
    u = User(tenant_id=tenant, email="u@x.io", name="U", token_version=0)
    db.add(u)
    db.commit()
    old = _tok(u.pk, 0, tenant)
    try:
        _set_flag(True)
        assert get_current_user(session_cookie=old).id == u.pk   # valid now
        u.token_version = 1                                       # logout-all / pw-change
        db.commit()
        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            get_current_user(session_cookie=old)                  # old token now dead
    finally:
        _set_flag(False)
