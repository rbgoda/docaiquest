"""M46 · §compliance · data-retention auto-purge.

Daily job: for each connected user, move server-stored originals older than
`documents_retention_purge_days` into their Drive, then purge the server blob
(re-pullable on demand) — so DocAIQ minimizes what it holds at rest. The derived
index (chunks/embeddings) stays; only the original file blob is moved. Off when
the setting is 0. Documents product only.
"""
from __future__ import annotations

import datetime as _dt
import logging

from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal, set_current_tenant
from app.documents_scope import set_current_owner_user_pk

log = logging.getLogger("docaiq.retention_purge")


async def retention_purge_task(ctx: dict) -> dict:
    s = get_settings()
    days = int(s.documents_retention_purge_days or 0)
    if s.product != "documents" or days <= 0:
        return {"status": "skipped", "reason": "disabled or not documents product"}

    from app.connectors import drive as drive_mod  # noqa: F401 (ensures backend importable)
    from app.orm import ConnectorAccount, Document
    from app.repositories import connectors as conn_repo
    from app.services import drive_backup

    # Timestamp comes from the worker context (Date.now() is unavailable in the
    # scripting sandbox; here we use the system clock in the worker process).
    cutoff = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=days)
    stats = {"accounts": 0, "purged": 0, "errors": 0}

    with SessionLocal() as session:
        accts = session.scalars(
            select(ConnectorAccount).where(ConnectorAccount.provider == "drive")
        ).all()
        for acct in accts:
            set_current_tenant(acct.tenant_id)
            set_current_owner_user_pk(acct.owner_user_id)
            try:
                if conn_repo.get(session, "drive") is None:
                    continue
                docs = session.scalars(
                    select(Document).where(
                        Document.owner_user_id == acct.owner_user_id,
                        Document.s3_key.is_not(None),
                        Document.created_at < cutoff,
                    )
                ).all()
                stats["accounts"] += 1
                for doc in docs:
                    try:
                        if await drive_backup.backup_doc_to_drive(session, doc):
                            stats["purged"] += 1
                    except Exception as e:  # noqa: BLE001
                        log.warning("retention: doc pk=%s purge failed: %s", doc.pk, e)
                        stats["errors"] += 1
            except Exception as e:  # noqa: BLE001
                log.warning("retention: account pk=%s failed: %s", acct.pk, e)
                stats["errors"] += 1
            finally:
                set_current_owner_user_pk(None)
                set_current_tenant(None)
    if stats["purged"] or stats["errors"]:
        log.info("retention_purge done: %s", stats)
    return stats
