"""Documents module seed — intentionally a no-op.

The Documents product is self-serve: users register and upload their own
documents; there are no audit / vendor / framework fixtures to load. Kept as a
stub so `app.main`'s lifespan import (`seed_tenant`) is satisfied.
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

log = logging.getLogger("docaiq.seed")


def seed_tenant(db: Session) -> None:
    """Self-serve product: no audit/vendor fixtures to load. But a fresh tenant
    still needs an LLM routing config — without one the router resolves every
    task to an empty plan (no model) and all classify/extract/chat LLM calls
    fail silently. Seed a key-appropriate default ONCE; never overwrite an
    existing (admin-tuned or already-seeded prod) config."""
    log.info("Documents module: no fixture seeding (self-serve product).")
    try:
        from app.config import get_settings
        from app.llm import default_routing
        from app.repositories import routing_configs as rc_repo

        if rc_repo.get(db) is not None:
            log.info("seed: LLM routing config already present; leaving it untouched.")
            return
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
    except Exception:  # noqa: BLE001 — seeding must never block tenant boot
        log.exception("seed: routing-config seeding failed (non-fatal).")
