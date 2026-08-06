"""Human-review columns on product_feedback: followup_needed + followup_note, and
their serialization in the admin feedback row."""
from __future__ import annotations

import os

import pytest

TEST_DB_URL = os.environ.get("DOCAIQ_TEST_DATABASE_URL")
T = "tenant-fbfu"


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


def test_followup_columns_default_and_roundtrip(db):
    from app.orm import ProductFeedback
    fb = ProductFeedback(tenant_id=T, category="bug", comments="x")
    db.add(fb)
    db.commit()
    assert fb.followup_needed is False and fb.followup_note is None   # additive defaults

    fb.followup_needed = True
    fb.followup_note = "the fix didn't cover mobile"
    db.commit()
    db.expire_all()
    row = db.get(ProductFeedback, fb.pk)
    assert row.followup_needed is True
    assert row.followup_note == "the fix didn't cover mobile"


def test_feedback_row_exposes_followup():
    from types import SimpleNamespace

    from app.routers.superadmin import _feedback_row
    f = SimpleNamespace(pk=1, email="e", rating=None, category="bug", comments="c",
                        suggestion=None, page="p", app_version=None, device_info=None,
                        screenshots=[], has_issues=False, status="in_progress",
                        resolution=None, followup_needed=True, followup_note="needs more",
                        ref=None, verified_by=None, verified_at=None, created_at=None,
                        reviewed_at=None)
    row = _feedback_row(f)
    assert row["followupNeeded"] is True and row["followupNote"] == "needs more"
