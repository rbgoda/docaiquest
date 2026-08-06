"""M46 · DOCAIQ_PRODUCT flag (auditing | documents). Pure config tests."""
from __future__ import annotations

import pytest
from pydantic import ValidationError


def test_product_defaults_to_auditing(monkeypatch):
    # the default only holds with no DOCAIQ_PRODUCT in the environment
    monkeypatch.delenv("DOCAIQ_PRODUCT", raising=False)
    from app.config import Settings
    assert Settings().product == "auditing"


def test_product_documents_via_env(monkeypatch):
    monkeypatch.setenv("DOCAIQ_PRODUCT", "documents")
    from app.config import Settings
    assert Settings().product == "documents"


def test_invalid_product_rejected():
    from app.config import Settings
    with pytest.raises(ValidationError):
        Settings(product="nonsense")
