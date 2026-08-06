"""Migration 0101 — rewrites schema_library rows to drop leaked JSON-Schema
definition-metadata keys ('required'/'description'/'type') that some Schema-Architect
drafts flattened into their top-level field map. Pure _clean tests always run; the
DB round-trip runs against real pgvector when DOCAIQ_TEST_DATABASE_URL is set."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

# Load the migration module by path (migrations/versions isn't an import package).
_MIG_PATH = (Path(__file__).resolve().parent.parent / "migrations" / "versions"
             / "20260713_0101_schema_lib_drop_leaked_meta.py")
_spec = importlib.util.spec_from_file_location("mig_0101", _MIG_PATH)
mig = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mig)


# ── pure: _clean ────────────────────────────────────────────────────────────

def test_clean_drops_leaked_metadata():
    dirty = {
        "id_number": {"type": "string", "required": True},
        "full_name": {"type": "string"},
        "required": False,          # leaked (bool)
        "description": "The ID.",   # leaked (str)
        "type": "object",           # leaked (str)
    }
    cleaned, changed = mig._clean(dirty)
    assert changed is True
    assert set(cleaned) == {"id_number", "full_name"}


def test_clean_keeps_real_field_named_like_metadata():
    fields = {"type": {"type": "string", "description": "doc type"}, "id": {"type": "string"}}
    cleaned, changed = mig._clean(fields)
    assert changed is False
    assert set(cleaned) == {"type", "id"}


def test_clean_noop_on_clean_schema():
    fields = {"a": {"type": "string"}, "b": {"type": "number", "required": True}}
    cleaned, changed = mig._clean(fields)
    assert changed is False and cleaned == fields


# ── DB integration ──────────────────────────────────────────────────────────

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
        s.rollback()
        s.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_run_rewrites_dirty_rows_only(db):
    from app.orm import SchemaLibrary
    dirty = SchemaLibrary(
        tenant_id="t", type_slug="national_id", label="National ID", version=1,
        status="approved", source="architect",
        fields={"id_number": {"type": "string"}, "required": False, "description": "x"})
    clean = SchemaLibrary(
        tenant_id="t", type_slug="invoice", label="Invoice", version=1,
        status="approved", source="architect",
        fields={"total": {"type": "number", "required": True}})
    db.add_all([dirty, clean])
    db.commit()
    dirty_pk, clean_pk = dirty.pk, clean.pk

    n = mig._run(db.connection())
    db.commit()
    assert n == 1  # only the dirty row rewritten

    db.expire_all()
    got_dirty = db.get(SchemaLibrary, dirty_pk)
    got_clean = db.get(SchemaLibrary, clean_pk)
    assert set(got_dirty.fields) == {"id_number"}          # leaks dropped
    assert got_clean.fields == {"total": {"type": "number", "required": True}}  # untouched
