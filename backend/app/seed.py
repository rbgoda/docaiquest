"""Documents module seed.

The Documents product is self-serve: users register and upload their own
documents; there are no audit / vendor / framework fixtures to load.

OSS mode seeds a demo user so the login page's "Try demo" button works
out-of-the-box without configuration.
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

log = logging.getLogger("docaiq.seed")

_DEMO_EMAIL = "demo@docaiquest.dev"
_DEMO_PASSWORD = "docaiquest"


def _seed_demo_user(db: Session) -> None:
    """Create the OSS demo user if it doesn't already exist."""
    from app.repositories import users as users_repo

    existing = users_repo.get_by_email(db, _DEMO_EMAIL)
    if existing:
        log.info("seed: demo user already exists; leaving untouched.")
        # Still ensure consent is recorded for the existing demo user.
        _ensure_demo_consent(db, existing.pk)
        return

    from app.auth import hash_password
    from app.config import get_settings

    settings = get_settings()
    # Only seed the demo user in OSS mode.
    if settings.license_mode != "oss":
        return

    try:
        user = users_repo.create(
            db,
            email=_DEMO_EMAIL,
            name="Demo User",
            roles=["owner"],
            password_hash=hash_password(_DEMO_PASSWORD),
        )
        db.flush()
        _record_demo_consent(db, user.pk)
        db.commit()
    except Exception:
        db.rollback()
        raise


def _record_demo_consent(db: Session, user_pk: int) -> None:
    """Record all consent kinds so the demo user can upload and chat immediately."""
    from app.config import get_settings
    from app.services import consent as consent_svc

    settings = get_settings()
    for kind in (consent_svc.KIND_PROCESSING, consent_svc.KIND_PERSONAL_DATA):
        try:
            consent_svc.record(db, tenant_id=settings.tenant_id, user_id=user_pk, kind=kind)
        except Exception:
            log.exception("seed: consent record failed for kind=%s", kind)


def _ensure_demo_consent(db: Session, user_pk: int) -> None:
    """Idempotent — record consent if any kind is missing for the demo user."""
    from app.config import get_settings
    from app.services import consent as consent_svc

    settings = get_settings()
    try:
        status = consent_svc.status(db, tenant_id=settings.tenant_id, user_id=user_pk)
    except Exception:
        status = {}

    if not status.get("processing") or not status.get("personalData"):
        _record_demo_consent(db, user_pk)
        db.commit()


def seed_tenant(db: Session) -> None:
    """Self-serve product: no audit/vendor fixtures to load. But a fresh tenant
    still needs an LLM routing config — without one the router resolves every
    task to an empty plan (no model) and all classify/extract/chat LLM calls
    fail silently. Seed a key-appropriate default ONCE; never overwrite an
    existing (admin-tuned or already-seeded prod) config.

    In OSS mode, also seeds a demo user so first-time visitors can click
    "Try demo" on the login page and explore immediately."""
    log.info("Documents module: no fixture seeding (self-serve product).")
    try:
        from app.config import get_settings
        from app.llm import default_routing
        from app.repositories import routing_configs as rc_repo

        if rc_repo.get(db) is not None:
            log.info("seed: LLM routing config already present; leaving it untouched.")
        else:
            s = get_settings()
            cfg = default_routing.pick_default_for_seed(
                openrouter_api_key=s.openrouter_api_key or "",
                google_api_key=s.google_genai_api_key or "",
                dashscope_api_key=s.dashscope_api_key or "",
            )
            if cfg:
                rc_repo.upsert(db, cfg)
                db.commit()
                log.info("seed: installed default LLM routing config (%d tiers).", len(cfg.get("tiers", [])))
            else:
                log.warning("seed: no LLM provider key set — routing config left empty "
                            "(classify/extract/chat will be unavailable until a key + config is added).")

        # OSS demo user — create once, never overwrite.
        _seed_demo_user(db)
    except Exception:  # noqa: BLE001 — seeding must never block tenant boot
        log.exception("seed: routing-config seeding failed (non-fatal).")
