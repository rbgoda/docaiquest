"""Shared test fixtures — env mocking, config isolation."""

import os
import sys
from unittest.mock import patch

import pytest

# Ensure backend/ is on the path so `from app.xxx` imports work.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture
def clean_env():
    """Remove all DOCAIQ_* env vars for the test, restore after."""
    saved = {k: v for k, v in os.environ.items() if k.startswith("DOCAIQ_")}
    for k in saved:
        del os.environ[k]
    yield
    for k, v in saved.items():
        os.environ[k] = v


@pytest.fixture
def oss_mode(clean_env):
    """Set DOCAIQ_LICENSE_MODE=oss."""
    os.environ["DOCAIQ_LICENSE_MODE"] = "oss"
    # Clear pydantic Settings cache so the env var takes effect
    from app.config import get_settings
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def cloud_mode(clean_env):
    """Set DOCAIQ_LICENSE_MODE=cloud."""
    os.environ["DOCAIQ_LICENSE_MODE"] = "cloud"
    from app.config import get_settings
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
