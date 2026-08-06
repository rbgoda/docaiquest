"""M46 · §5 · auto-sync the per-user docaiq_docs Drive inbox.

Periodically pulls new files from each connected user's docaiq_docs folder into
their workspace (dedup by sha256, ingest enqueued) — so dropping a file in Drive
is enough, no manual sync. Moves toward Drive-as-the-drop-point (the "users own
everything" vision). Documents product only; gated by documents_drive_autosync.
Idempotent: already-synced files are skipped by content hash.
"""
from __future__ import annotations

import logging

from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal, set_current_tenant
from app.documents_scope import set_current_owner_user_pk

log = logging.getLogger("docaiq.drive_autosync")


class _StubUser:
    """Minimal stand-in for the request user — _sync_folder only reads .email
    (for uploaded_by attribution)."""
    def __init__(self, email: str):
        self.email = email


async def drive_autosync_task(ctx: dict) -> dict:
    s = get_settings()
    stats = {"accounts": 0, "created": 0, "skipped": 0, "errors": 0}
    from app.license import is_cloud
    if s.product != "documents" or not s.documents_drive_autosync or not is_cloud():
        return {"status": "skipped", "reason": "disabled, not documents product, or oss license"}

    from app.connectors import drive as drive_mod
    from app.orm import ConnectorAccount
    from app.routers.connectors import _sync_folder  # reuse the proven sync path

    with SessionLocal() as session:
        accts = session.scalars(
            select(ConnectorAccount).where(ConnectorAccount.provider == "drive")
        ).all()
        backend = drive_mod.get_backend()
        for acct in accts:
            set_current_tenant(acct.tenant_id)
            set_current_owner_user_pk(acct.owner_user_id)
            try:
                fid = await backend.find_or_create_folder(acct, drive_mod.INBOX_FOLDER_NAME)
                email = acct.account_email or f"user:{acct.owner_user_id}"
                # keep_original=True · auto-sync never purges (retention is a
                # separate, explicit user action).
                summary = await _sync_folder(session, acct, backend, fid, True, _StubUser(email))
                stats["accounts"] += 1
                stats["created"] += len(summary.created)
                stats["skipped"] += len(summary.skipped)
                stats["errors"] += len(summary.errors)
                if summary.created:
                    log.info("drive_autosync: owner=%s pulled %d new file(s)",
                             acct.owner_user_id, len(summary.created))
            except Exception as e:  # noqa: BLE001 — one bad account never stalls the rest
                log.warning("drive_autosync: account pk=%s failed: %s", acct.pk, e)
                stats["errors"] += 1
            finally:
                set_current_owner_user_pk(None)
                set_current_tenant(None)
    if stats["created"] or stats["errors"]:
        log.info("drive_autosync done: %s", stats)
    return stats
