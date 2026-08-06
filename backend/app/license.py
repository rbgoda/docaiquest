"""License-mode helpers.

Provides the single source of truth for oss-vs-cloud feature gating.
``license_mode`` is a deployment-wide constant (env var DOCAIQ_LICENSE_MODE) —
it never changes per tenant or per user, unlike feature flags or plan tiers.
"""
from __future__ import annotations

from app.config import get_settings


def is_cloud() -> bool:
    """Return True when the deployment is DocAIQ Cloud (premium proxy-based)."""
    return get_settings().license_mode == "cloud"
