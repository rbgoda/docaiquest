"""M44.P13 PR2 · tenant-side knowledge promoter tests.

Pure tests (token determinism, gating) always run. The collect/contribute
DB-integration test runs against real pgvector when DOCAIQ_TEST_DATABASE_URL
is set, else skips (same harness as test_learning_promoter.py)."""
from __future__ import annotations

import os

import pytest

from app.services import knowledge_promoter as kp


# ── pure: opaque token ─────────────────────────────────────────────────────

def test_tenant_token_stable_and_distinct():
    a1 = kp.tenant_token("acme", "salt")
    a2 = kp.tenant_token("acme", "salt")
    b = kp.tenant_token("globex", "salt")
    assert a1 == a2            # stable per tenant
    assert a1 != b             # distinct across tenants
    assert len(a1) == 32
    assert "acme" not in a1    # opaque — slug not embedded


def test_tenant_token_salt_changes_token():
    assert kp.tenant_token("acme", "salt1") != kp.tenant_token("acme", "salt2")


# ── pure: gating (no DB needed — returns before any query) ─────────────────

def test_promote_skips_when_contribute_disabled(monkeypatch):
    from app.config import get_settings
    s = get_settings()
    monkeypatch.setattr(s, "contribute_learning", False)
    out = kp.promote_to_global(db=None, tenant_id="acme")  # db unused on this path
    assert out["status"] == "skipped" and out["contributed"] == 0


def test_promote_skips_when_no_cp_url(monkeypatch):
    from app.config import get_settings
    s = get_settings()
    monkeypatch.setattr(s, "contribute_learning", True)
    monkeypatch.setattr(s, "control_plane_internal_url", "")
    out = kp.promote_to_global(db=None, tenant_id="acme")
    assert out["status"] == "skipped"


# ── DB integration: collect + contribute ───────────────────────────────────

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


T = "tenant-kp"


def test_collect_skeletons_selects_local_and_wraps_shape(db):
    from app.orm import ExtractionCorrection, AgentSkillMemory
    # promotable LOCAL rows
    db.add(ExtractionCorrection(tenant_id=T, doc_type="invoice", pattern_kind="frequent_mismatch",
                                pattern={"wrong_field": "fields.total"}, source="local"))
    db.add(AgentSkillMemory(tenant_id=T, doc_type="kyc_passport",
                            question_template="what is the {id_field}?",
                            tool_sequence=["search_chunks", "final_answer"], source="local"))
    # a GLOBAL (seeded) row must be EXCLUDED — no echo loop
    db.add(ExtractionCorrection(tenant_id=T, doc_type="invoice", pattern_kind="frequent_mismatch",
                                pattern={"wrong_field": "fields.tax"}, source="global"))
    db.flush()

    out = kp.collect_skeletons(db, T)
    kinds = sorted(s["kind"] for s in out)
    assert kinds == ["agent_skill", "extraction_correction"]   # 2, global excluded
    for s in out:
        assert set(s.keys()) == {"kind", "doc_type", "skeleton"}   # CP contrib shape
    ec = next(s for s in out if s["kind"] == "extraction_correction")
    assert ec["skeleton"]["pattern"]["wrong_field"] == "fields.total"
    sk = next(s for s in out if s["kind"] == "agent_skill")
    assert sk["skeleton"]["tool_sequence"] == ["search_chunks", "final_answer"]


def test_promote_to_global_posts_payload(db, monkeypatch):
    from app.orm import ExtractionCorrection
    from app.config import get_settings
    db.add(ExtractionCorrection(tenant_id=T, doc_type="invoice", pattern_kind="frequent_mismatch",
                                pattern={"wrong_field": "fields.total"}, source="local"))
    db.flush()

    s = get_settings()
    monkeypatch.setattr(s, "contribute_learning", True)
    monkeypatch.setattr(s, "control_plane_internal_url", "http://cp.test")

    captured = {}

    class _Resp:
        def raise_for_status(self): pass
        def json(self): return {"accepted": 1, "rejected": 0}

    def _fake_post(url, json=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        return _Resp()

    monkeypatch.setattr(kp.httpx, "post", _fake_post)

    out = kp.promote_to_global(db, T)
    assert out["status"] == "ok" and out["contributed"] == 1
    assert captured["url"].endswith("/api/platform/knowledge/contrib")
    body = captured["json"]
    assert "tenant_token" in body and len(body["skeletons"]) == 1
    assert T not in body["tenant_token"]   # opaque
