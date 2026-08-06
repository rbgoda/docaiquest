"""M44.P13 PR3 · knowledge seeder (receive side) tests.

Pure gating tests always run. The seed DB-integration tests run against real
pgvector when DOCAIQ_TEST_DATABASE_URL is set (same harness as the others)."""
from __future__ import annotations

import os

import pytest

from app.services import knowledge_seeder as ks


# ── pure: gating ───────────────────────────────────────────────────────────

def test_sync_skips_when_receive_disabled(monkeypatch):
    from app.config import get_settings
    s = get_settings()
    monkeypatch.setattr(s, "receive_global_learning", False)
    out = ks.sync_from_global(db=None, tenant_id="acme")
    assert out["status"] == "skipped" and out["seeded"] == 0


def test_sync_skips_when_no_cp_url(monkeypatch):
    from app.config import get_settings
    s = get_settings()
    monkeypatch.setattr(s, "receive_global_learning", True)
    monkeypatch.setattr(s, "control_plane_internal_url", "")
    out = ks.sync_from_global(db=None, tenant_id="acme")
    assert out["status"] == "skipped"


# ── DB integration ─────────────────────────────────────────────────────────

TEST_DB_URL = os.environ.get("DOCAIQ_TEST_DATABASE_URL")


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
        s.rollback(); s.close()
        Base.metadata.drop_all(engine); engine.dispose()


T = "tenant-seed"

_ACTIVE = [
    {"kind": "extraction_correction", "doc_type": "invoice",
     "skeleton": {"pattern": {"wrong_field": "fields.total"}}},
    {"kind": "agent_skill", "doc_type": "kyc_passport",
     "skeleton": {"question_template": "what is the {id_field}?",
                  "tool_sequence": ["search_chunks", "final_answer"]}},
]


def _patch_active(monkeypatch, items):
    from app.config import get_settings
    s = get_settings()
    monkeypatch.setattr(s, "receive_global_learning", True)
    monkeypatch.setattr(s, "control_plane_internal_url", "http://cp.test")

    class _Resp:
        def raise_for_status(self): pass
        def json(self): return items

    monkeypatch.setattr(ks.httpx, "get", lambda url, timeout=None: _Resp())


def test_sync_seeds_global_rows(db, monkeypatch):
    from app.orm import ExtractionCorrection, AgentSkillMemory
    _patch_active(monkeypatch, _ACTIVE)

    out = ks.sync_from_global(db, T)
    assert out["status"] == "ok" and out["seeded"] == 2

    ec = db.query(ExtractionCorrection).filter(ExtractionCorrection.tenant_id == T).all()
    assert len(ec) == 1 and ec[0].source == "global" and ec[0].pattern["wrong_field"] == "fields.total"
    sk = db.query(AgentSkillMemory).filter(AgentSkillMemory.tenant_id == T).all()
    assert len(sk) == 1 and sk[0].source == "global" and sk[0].tool_sequence == ["search_chunks", "final_answer"]


def test_sync_is_idempotent(db, monkeypatch):
    from app.orm import ExtractionCorrection, AgentSkillMemory
    _patch_active(monkeypatch, _ACTIVE)
    ks.sync_from_global(db, T)
    second = ks.sync_from_global(db, T)            # re-sync
    assert second["seeded"] == 0                    # nothing new
    assert db.query(ExtractionCorrection).count() == 1
    assert db.query(AgentSkillMemory).count() == 1


def test_sync_is_local_first(db, monkeypatch):
    """A tenant's own earned (local) row must NOT be overwritten/duplicated by
    a global row with the same key."""
    from app.orm import ExtractionCorrection
    db.add(ExtractionCorrection(
        tenant_id=T, doc_type="invoice", pattern_kind="frequent_mismatch",
        pattern={"wrong_field": "fields.total"}, source="local",
    ))
    db.flush()
    _patch_active(monkeypatch, _ACTIVE)

    out = ks.sync_from_global(db, T)
    # the extraction_correction was skipped (local present); only the agent_skill seeded
    assert out["seeded"] == 1
    rows = db.query(ExtractionCorrection).filter(ExtractionCorrection.tenant_id == T).all()
    assert len(rows) == 1 and rows[0].source == "local"   # local preserved, not clobbered
