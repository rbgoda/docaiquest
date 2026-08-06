"""Auto-triage drafts a resolution but must NOT flip status to in_progress — a new
item stays 'new' until a human picks it up (feedback pk 44)."""
from __future__ import annotations

import os

import pytest

TEST_DB_URL = os.environ.get("DOCAIQ_TEST_DATABASE_URL")
T = "tenant-fbtriage"


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


def test_triage_drafts_resolution_but_keeps_status_new(db, monkeypatch):
    from app.db import set_current_tenant
    from app.orm import ProductFeedback
    from app.services import feedback_triage
    set_current_tenant(T)
    fb = ProductFeedback(tenant_id=T, category="bug", comments="button overlaps")
    db.add(fb)
    db.commit()
    assert fb.status == "new"

    # Stub the LLM so the test is hermetic.
    monkeypatch.setattr(feedback_triage, "classify_and_draft",
                        lambda fbdict, tid: {"severity": "low", "area": "ui", "resolution": "align the button"})
    feedback_triage.triage_feedback(fb.pk, T)

    db.expire_all()
    row = db.get(ProductFeedback, fb.pk)
    assert row.status == "new"                       # NOT flipped to in_progress
    assert row.resolution and "align the button" in row.resolution   # draft written
    assert row.resolution.startswith("🤖 Auto-triage")
