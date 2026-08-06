"""Correctness fixes for two default-on deterministic chat handlers.

  · _parse_amount: took the LAST numeric token, so a trailing VAT %, item count or
    multiplier beat the real amount ('4,080.00 (VAT 8%)' -> 8). Now prefers the
    largest money-shaped token.
  · _answer_count_or_dates: 'how many statements' returned only the FIRST matching
    doc_type's count (undercount). Now sums across all matching types.
"""
from __future__ import annotations

import os

import pytest

from app.services.workspace_handlers import _parse_amount

TEST_DB_URL = os.environ.get("DOCAIQ_TEST_DATABASE_URL")
T = "tenant-handlers"


# ── _parse_amount (pure) ──────────────────────────────────────────────────────
def test_parse_amount_ignores_trailing_noise():
    assert _parse_amount("4,080.00 (VAT 8%)") == 4080.0     # was 8.0
    assert _parse_amount("$100 x 2") == 100.0               # was 2.0
    assert _parse_amount("500.00 for 2 items") == 500.0     # was 2.0


def test_parse_amount_clean_values_unchanged():
    assert _parse_amount("4,080.00") == 4080.0
    assert _parse_amount("SGD 4,080.00") == 4080.0
    assert _parse_amount("$1,234.56") == 1234.56
    assert _parse_amount(4080) == 4080.0
    assert _parse_amount(4080.5) == 4080.5


def test_parse_amount_non_amounts():
    assert _parse_amount("n/a") is None
    assert _parse_amount(None) is None
    assert _parse_amount(True) is None


# ── _answer_count_or_dates (DB) ───────────────────────────────────────────────
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


def _mk(db, owner_pk, idx, doc_type):
    from app.orm import Document
    d = Document(tenant_id=T, id_external=idx, name=f"doc {idx}", path=f"/{idx}",
                 size="1", modified="2026-01-01", pages=1, current_page=1,
                 type="pdf", content="pdf", ingestion_status="ready",
                 owner_user_id=owner_pk, doc_type=doc_type)
    db.add(d)
    db.flush()
    return d


def test_count_sums_across_matching_types(db):
    from app.documents_scope import set_current_owner_user_pk
    from app.services.workspace_handlers import _answer_count_or_dates
    u = _mk_user(db, "a@x.io")
    set_current_owner_user_pk(u.pk)
    _mk(db, u.pk, "a", "bank_account_statement")
    _mk(db, u.pk, "b", "bank_account_statement")
    _mk(db, u.pk, "c", "credit_card_statement")
    db.commit()
    ans = _answer_count_or_dates(db, T, "how many statements do I have?")
    assert ans is not None
    assert "**3**" in ans           # 2 bank + 1 credit-card, summed (was 2)


def test_count_single_type_exact(db):
    from app.documents_scope import set_current_owner_user_pk
    from app.services.workspace_handlers import _answer_count_or_dates
    u = _mk_user(db, "b@x.io")
    set_current_owner_user_pk(u.pk)
    _mk(db, u.pk, "x", "invoice")
    _mk(db, u.pk, "y", "invoice")
    db.commit()
    ans = _answer_count_or_dates(db, T, "how many invoices do I have?")
    assert ans is not None and "**2**" in ans


# ── _doc_currency (pure) ──────────────────────────────────────────────────────
def test_doc_currency_detection():
    from types import SimpleNamespace
    from app.services.workspace_handlers import _doc_currency
    mk = lambda fields: SimpleNamespace(extracted_fields={"fields": fields}, pk=1, name="d")  # noqa: E731
    assert _doc_currency(mk({"currency": "USD"})) == "USD"
    assert _doc_currency(mk({"currency": "$"})) == "USD"
    assert _doc_currency(mk({"total": "SGD 500.00"})) == "SGD"
    assert _doc_currency(mk({"total": "$100.00"})) == "USD"
    assert _doc_currency(mk({"total": "500.00"})) == ""      # no currency → unknown


def _mk_priced(db, owner_pk, idx, total, currency, doc_type="invoice"):
    from app.orm import Document
    d = Document(tenant_id=T, id_external=idx, name=f"doc {idx}", path=f"/{idx}",
                 size="1", modified="2026-01-01", pages=1, current_page=1,
                 type="pdf", content="pdf", ingestion_status="ready",
                 owner_user_id=owner_pk, doc_type=doc_type,
                 extracted_fields={"fields": {"total": total, "currency": currency}})
    db.add(d)
    db.flush()
    return d


def test_money_mixed_currency_kept_separate(db):
    from app.documents_scope import set_current_owner_user_pk
    from app.services.workspace_handlers import _answer_money
    u = _mk_user(db, "m@x.io")
    set_current_owner_user_pk(u.pk)
    _mk_priced(db, u.pk, "u1", "100.00", "USD")
    _mk_priced(db, u.pk, "s1", "100.00", "SGD")
    db.commit()
    ans = _answer_money(db, T, "what is the combined total across all my invoices?")
    assert ans is not None
    assert "2 currencies" in ans          # split, not summed
    assert "USD: 100.00" in ans and "SGD: 100.00" in ans
    assert "200.00" not in ans            # the meaningless cross-currency sum is gone


def test_money_single_currency_combined(db):
    from app.documents_scope import set_current_owner_user_pk
    from app.services.workspace_handlers import _answer_money
    u = _mk_user(db, "n@x.io")
    set_current_owner_user_pk(u.pk)
    _mk_priced(db, u.pk, "a1", "100.00", "USD")
    _mk_priced(db, u.pk, "a2", "250.50", "USD")
    db.commit()
    ans = _answer_money(db, T, "combined total across all my invoices")
    assert ans is not None
    assert "350.50 USD" in ans            # single currency → one labelled total
