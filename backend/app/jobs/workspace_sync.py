"""M46 · §5 · nightly per-user workspace sync to Drive.

Rebuilds each connected user's encrypted workspace.sqlite and pushes it to their
Drive, so "your data in your Drive" stays current without a manual click.
Documents product only; gated by documents_workspace_autosync (default off, since
it writes to each user's real Drive).
"""
from __future__ import annotations

import logging

from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal, set_current_tenant
from app.documents_scope import set_current_owner_user_pk

log = logging.getLogger("docaiq.workspace_sync_job")


async def workspace_sync_task(ctx: dict) -> dict:
    s = get_settings()
    if s.product != "documents" or not getattr(s, "documents_workspace_autosync", False):
        return {"status": "skipped", "reason": "disabled or not documents product"}
    from app.orm import ConnectorAccount
    from app.services import workspace_export
    stats = {"users": 0, "errors": 0}
    with SessionLocal() as db:
        accts = db.scalars(select(ConnectorAccount).where(ConnectorAccount.provider == "drive")).all()
        for acct in accts:
            set_current_tenant(acct.tenant_id)
            set_current_owner_user_pk(acct.owner_user_id)
            try:
                await workspace_export.sync_to_drive(
                    db, tenant_id=acct.tenant_id, owner_user_id=acct.owner_user_id)
                stats["users"] += 1
            except Exception as e:  # noqa: BLE001
                log.warning("workspace_sync: owner=%s failed: %s", acct.owner_user_id, e)
                stats["errors"] += 1
            finally:
                set_current_owner_user_pk(None)
                set_current_tenant(None)
    if stats["users"] or stats["errors"]:
        log.info("workspace_sync done: %s", stats)
    return stats
