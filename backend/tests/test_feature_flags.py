"""Tests for feature_flags.py — flag resolution order.

Tests the resolution chain: env var → default, without DB overrides (which
require a running postgres)."""

import os
import pytest
from app.feature_flags import is_enabled, get_int, get_str, get_float, _read_env


class TestReadEnv:
    def test_explicit_env_var(self, clean_env):
        os.environ["MY_CUSTOM_VAR"] = "hello"
        assert _read_env("some_flag", env_var="MY_CUSTOM_VAR") == "hello"

    def test_docaiq_prefix(self, clean_env):
        os.environ["DOCAIQ_MY_FLAG"] = "world"
        assert _read_env("my_flag") == "world"

    def test_bare_name(self, clean_env):
        os.environ["MY_FLAG"] = "bare"
        assert _read_env("my_flag") == "bare"

    def test_order_explicit_first(self, clean_env):
        os.environ["EXPLICIT"] = "first"
        os.environ["DOCAIQ_MY_FLAG"] = "second"
        assert _read_env("my_flag", env_var="EXPLICIT") == "first"

    def test_none_set(self, clean_env):
        assert _read_env("nonexistent_flag") == ""


class TestIsEnabled:
    def test_default_false(self, clean_env):
        from app.feature_flags import invalidate_feature_flags_cache
        invalidate_feature_flags_cache()
        assert is_enabled("nonexistent_flag") is False

    def test_default_true(self, clean_env):
        from app.feature_flags import invalidate_feature_flags_cache
        invalidate_feature_flags_cache()
        assert is_enabled("nonexistent_flag", default=True) is True

    def test_env_var_true(self, clean_env):
        from app.feature_flags import invalidate_feature_flags_cache
        invalidate_feature_flags_cache()
        os.environ["DOCAIQ_TEST_FLAG"] = "true"
        assert is_enabled("test_flag") is True

    def test_env_var_1(self, clean_env):
        from app.feature_flags import invalidate_feature_flags_cache
        invalidate_feature_flags_cache()
        os.environ["DOCAIQ_TEST_FLAG"] = "1"
        assert is_enabled("test_flag") is True

    def test_env_var_false(self, clean_env):
        from app.feature_flags import invalidate_feature_flags_cache
        invalidate_feature_flags_cache()
        os.environ["DOCAIQ_TEST_FLAG"] = "false"
        assert is_enabled("test_flag") is False

    def test_env_var_yes(self, clean_env):
        from app.feature_flags import invalidate_feature_flags_cache
        invalidate_feature_flags_cache()
        os.environ["DOCAIQ_TEST_FLAG"] = "yes"
        assert is_enabled("test_flag") is True

    def test_env_var_on(self, clean_env):
        from app.feature_flags import invalidate_feature_flags_cache
        invalidate_feature_flags_cache()
        os.environ["DOCAIQ_TEST_FLAG"] = "on"
        assert is_enabled("test_flag") is True

    def test_env_var_off(self, clean_env):
        from app.feature_flags import invalidate_feature_flags_cache
        invalidate_feature_flags_cache()
        os.environ["DOCAIQ_TEST_FLAG"] = "off"
        assert is_enabled("test_flag") is False


class TestGetInt:
    def test_default(self, clean_env):
        from app.feature_flags import invalidate_feature_flags_cache
        invalidate_feature_flags_cache()
        assert get_int("nonexistent", default=42) == 42

    def test_env_var(self, clean_env):
        from app.feature_flags import invalidate_feature_flags_cache
        invalidate_feature_flags_cache()
        os.environ["DOCAIQ_MAX_ITEMS"] = "100"
        assert get_int("max_items") == 100

    def test_non_numeric_env_var_falls_to_default(self, clean_env):
        from app.feature_flags import invalidate_feature_flags_cache
        invalidate_feature_flags_cache()
        os.environ["DOCAIQ_MAX_ITEMS"] = "abc"
        assert get_int("max_items", default=10) == 10


class TestGetStr:
    def test_default(self, clean_env):
        from app.feature_flags import invalidate_feature_flags_cache
        invalidate_feature_flags_cache()
        assert get_str("nonexistent", default="fallback") == "fallback"

    def test_env_var(self, clean_env):
        from app.feature_flags import invalidate_feature_flags_cache
        invalidate_feature_flags_cache()
        os.environ["DOCAIQ_MODEL_NAME"] = "qwen-max"
        assert get_str("model_name") == "qwen-max"


class TestGetFloat:
    def test_default(self, clean_env):
        from app.feature_flags import invalidate_feature_flags_cache
        invalidate_feature_flags_cache()
        assert get_float("nonexistent", default=0.5) == 0.5

    def test_env_var(self, clean_env):
        from app.feature_flags import invalidate_feature_flags_cache
        invalidate_feature_flags_cache()
        os.environ["DOCAIQ_THRESHOLD"] = "0.75"
        assert get_float("threshold") == 0.75

    def test_non_numeric_falls_to_default(self, clean_env):
        from app.feature_flags import invalidate_feature_flags_cache
        invalidate_feature_flags_cache()
        os.environ["DOCAIQ_THRESHOLD"] = "abc"
        assert get_float("threshold", default=0.3) == 0.3
