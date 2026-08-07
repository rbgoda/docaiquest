"""Tests for license.py — deployment-wide oss/cloud gating."""

import os
import pytest
from app.license import is_cloud


class TestIsCloud:
    def test_oss_by_default(self, clean_env):
        # No env var → defaults to oss
        from app.config import get_settings
        get_settings.cache_clear()
        try:
            assert is_cloud() is False
        finally:
            get_settings.cache_clear()

    def test_oss_explicit(self, oss_mode):
        assert is_cloud() is False

    def test_cloud_explicit(self, cloud_mode):
        assert is_cloud() is True
