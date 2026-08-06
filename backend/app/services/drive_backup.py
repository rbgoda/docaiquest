"""M46 · Documents · back up a server-stored upload to the user's own Google
Drive, then purge the server copy — so Drive becomes the store of record and
server storage stays flat regardless of how much a user uploads.

Flow per doc: read the server blob → find-or-create the user's `docaiq_docs`
folder → upload the blob there → flip the doc to source='drive' + source_ref →
delete the server blob. The existing download_file auto-re-pull then restores it
from Drive on demand. Owner scope must be set by the caller (middleware for the
API path; explicitly for the worker path).
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app import storage
from app.connectors import drive as drive_mod
from app.repositories import connectors as conn_repo

log = logging.getLogger("docaiq.drive_backup")


def _purge_server_blob(db: Session, doc) -> None:
    """Drop the server copy of a doc that's already safe in Drive (re-pullable).
    Clears s3_key first (so the row is re-pull-only) then deletes the object."""
    old_key = doc.s3_key
    doc.retain_original = False
    doc.s3_key = None
    db.commit()
    try:
        storage.delete_object(old_key)
    except Exception:  # noqa: BLE001 — best-effort; row already points at Drive
        log.warning("drive_backup: blob purge failed for doc pk=%s (orphaned blob)", doc.pk)


async def backup_doc_to_drive(db: Session, doc) -> bool:
    """Free the server copy of `doc`, keeping it available from the user's Drive.

    - Already in Drive (source='drive' + source_ref): just purge the server copy.
    - Direct upload (source NULL/upload): push the blob to docaiq_docs, flip to
      source='drive', then purge.
    Returns True if a server copy was freed, False if skipped (no blob, no
    re-pull path, or Drive not connected for an upload).
    """
    if not doc.s3_key:
        return False  # nothing on the server to free

    # Case A · already in Drive → just purge the redundant server copy.
    if doc.source == "drive":
        if not doc.source_ref:
            return False  # no re-pull handle — keep the server copy to be safe
        _purge_server_blob(db, doc)
        log.info("drive_backup: doc pk=%s server copy purged (already in Drive)", doc.pk)
        return True

    # Case B · direct upload → push to Drive, flip, then purge.
    acct = conn_repo.get(db, "drive")  # owner-scoped — caller sets owner ctx
    if acct is None:
        return False  # Drive not connected → leave the server copy in place
    blob = storage.get_object_bytes(doc.s3_key)
    if blob is None:
        return False
    # Hardened storage form (replaces the old server-JWT `encrypt_files` path,
    # which a deploy/account-recreate could orphan):
    #   · default → PLAINTEXT in the user's own private Drive (always re-readable)
    #   · opt-in  → encrypted with the user's password-derived key (user owns it)
    # If encryption is enabled but locked (no cached key), skip — never write
    # plaintext against the user's wish, and never purge.
    from app import backup_keycache, drive_crypto
    from app.db import get_current_tenant
    from app.orm import User
    u = db.get(User, doc.owner_user_id)
    pw_key = None
    if u is not None and getattr(u, "backup_encryption", False):
        pw_key = backup_keycache.get(get_current_tenant(), doc.owner_user_id)
        if pw_key is None:
            log.info("drive_backup: doc pk=%s skipped — backup encryption locked", doc.pk)
            return False
    out_blob = drive_crypto.encrypt_blob_pw(blob, pw_key) if pw_key else blob

    backend = drive_mod.get_backend()
    folder_id = await backend.find_or_create_folder(acct, drive_mod.INBOX_FOLDER_NAME)
    file_id = await backend.upload_file(
        acct, doc.name, out_blob, doc.mime_type or "application/octet-stream", folder_id)

    # VERIFY-BEFORE-PURGE: re-fetch the Drive copy, decrypt it, and confirm it
    # matches the original bytes. We must NEVER delete the only readable copy.
    verified = False
    try:
        pulled = await backend.fetch(acct, file_id)
        check = drive_crypto.decrypt_blob_pw(pulled.body, pw_key) if pw_key else pulled.body
        verified = (check == blob)
    except Exception as e:  # noqa: BLE001
        log.warning("drive_backup: verify failed for doc pk=%s: %s", doc.pk, e)

    if not verified:
        # Don't flip to Drive and don't purge — keep serving from the server.
        # Remove the unverified Drive upload so we don't leave a bad shell.
        try:
            await backend.delete_file(acct, file_id)
        except Exception:  # noqa: BLE001
            pass
        log.warning("drive_backup: doc pk=%s NOT freed — Drive copy didn't verify; server copy kept", doc.pk)
        return False

    # Verified re-readable → flip to Drive-backed, then purge the server copy.
    doc.source = "drive"
    doc.source_ref = file_id
    _purge_server_blob(db, doc)
    log.info("drive_backup: doc pk=%s pushed to Drive (%s), verified, server copy purged", doc.pk, file_id)
    return True
