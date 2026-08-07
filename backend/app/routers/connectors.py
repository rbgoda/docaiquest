"""M46 · Documents System · Google Drive connector endpoints.

Documents-product only (404 elsewhere). Per-user: a user connects their own
Drive, browses their own folders, and syncs files into their own private
workspace (owner-scoped exactly like uploads). Retention default is
download→process→purge→keep re-pull link; keep-original is opt-in.

  GET    /api/connectors/drive                 → connection status
  POST   /api/connectors/drive/connect         → stub: connect now · google: {authUrl}
  GET    /api/connectors/drive/callback        → google OAuth landing (stores token)
  DELETE /api/connectors/drive                 → disconnect
  GET    /api/connectors/drive/folders         → list folders
  GET    /api/connectors/drive/folders/{id}/files → list files in a folder
  POST   /api/connectors/drive/sync            → pull a folder's files → ingest
"""
from __future__ import annotations

import logging
import secrets

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.connectors import drive as drive_mod
from app.db import get_session, get_current_tenant
from app.documents_scope import get_current_owner_user_pk
from app.link_pull import LinkPullError
from app.queue import enqueue_ingest
from app.repositories import connectors as conn_repo
from app.repositories import documents as repo
from app.security import CurrentUser, get_current_user

log = logging.getLogger("docaiq.connectors")
router = APIRouter()


def _guard() -> None:
    """Documents-product + feature-flag gate. 404 (not 403) elsewhere so the
    connector surface is simply invisible in the auditing product."""
    s = get_settings()
    if s.product != "documents" or not s.documents_drive_connector:
        raise HTTPException(status_code=404, detail="Connector not available")


def _redirect_uri() -> str:
    return f"{get_settings().public_url.rstrip('/')}/api/connectors/drive/callback"


def _human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n/1024:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


# ── status / connect / disconnect ───────────────────────────────────────────
@router.get("/connectors/drive")
def drive_status(db: Session = Depends(get_session),
                 user: CurrentUser = Depends(get_current_user)) -> dict:
    _guard()
    backend = drive_mod.get_backend()
    acct = conn_repo.get(db, "drive")
    from app.orm import User
    u = db.get(User, get_current_owner_user_pk())
    return {
        "provider": "drive",
        "backend": backend.name,
        "requiresOauth": backend.requires_oauth,
        "connected": acct is not None,
        "accountEmail": acct.account_email if acct else None,
        "encryptFiles": bool(acct.encrypt_files) if acct else False,
        "backupEncryption": bool(getattr(u, "backup_encryption", False)),
    }


class EncryptionPref(BaseModel):
    enabled: bool


@router.post("/connectors/drive/encryption")
async def set_drive_encryption(payload: EncryptionPref,
                               db: Session = Depends(get_session),
                               user: CurrentUser = Depends(get_current_user)) -> dict:
    """B7 · per-user 'encrypt my Drive files' toggle. Turning it ON re-encrypts
    the user's existing Drive-stored files in place (openable only via DocAIQ).
    Turning it OFF leaves already-encrypted files as-is (they still decrypt on
    read) and stops encrypting new ones."""
    _guard()
    acct = _require_account(db)
    if payload.enabled:  # M47 · encryption is a Pro feature
        from app.services import subscriptions as subs
        subs.enforce_feature(db, owner_user_id=get_current_owner_user_pk(), feature="encryption")
    acct.encrypt_files = bool(payload.enabled)
    db.commit()
    result = {"enabled": acct.encrypt_files, "reencrypted": 0, "errors": 0}
    if payload.enabled:
        result.update(await _reencrypt_existing(db, acct))
    return result


async def _reencrypt_existing(db: Session, acct) -> dict:
    """Download each of the user's Drive-stored files, encrypt it, re-upload as a
    replacement, and point the doc at the new (encrypted) file."""
    from app import drive_crypto
    from app.documents_scope import get_current_owner_user_pk
    from app.orm import Document
    from sqlalchemy import select as _sel
    backend = drive_mod.get_backend()
    uid = get_current_owner_user_pk()
    done = 0
    errors = 0
    inbox = await backend.find_or_create_folder(acct, drive_mod.INBOX_FOLDER_NAME)
    docs = db.scalars(_sel(Document).where(
        Document.owner_user_id == uid, Document.source == "drive",
        Document.source_ref.is_not(None), Document.is_archived.is_(False))).all()
    for doc in docs:
        try:
            pulled = await backend.fetch(acct, doc.source_ref)
            if drive_crypto.is_encrypted(pulled.body):
                continue  # already encrypted
            enc = drive_crypto.encrypt_blob(uid, pulled.body, enabled=True)
            new_id = await backend.upload_file(
                acct, doc.name, enc, doc.mime_type or "application/octet-stream", inbox)
            try:
                await backend.delete_file(acct, doc.source_ref)
            except Exception:  # noqa: BLE001
                pass
            doc.source_ref = new_id
            done += 1
        except Exception as e:  # noqa: BLE001
            log.warning("reencrypt: doc pk=%s failed: %s", doc.pk, e)
            errors += 1
    db.commit()
    log.info("reencrypt: owner=%s · %d files encrypted · %d errors", uid, done, errors)
    return {"reencrypted": done, "errors": errors}


_DRIVE_STATE_COOKIE = "docaiq_drive_oauth_state"


@router.post("/connectors/drive/connect")
def drive_connect(response: Response, db: Session = Depends(get_session),
                  user: CurrentUser = Depends(get_current_user)) -> dict:
    _guard()
    backend = drive_mod.get_backend()
    if not backend.requires_oauth:
        # Stub backend — connect instantly, no OAuth round-trip.
        tok = backend.instant_token()
        conn_repo.upsert(db, provider="drive", backend=backend.name,
                         access_token=tok.access_token, refresh_token=tok.refresh_token,
                         account_email=tok.account_email)
        return {"connected": True, "authUrl": None, "backend": backend.name}
    # Google — hand back the consent URL. State carries a CSRF nonce that we ALSO
    # stash in an HttpOnly cookie; the callback compares the two (§4).
    state = secrets.token_urlsafe(24)
    url = backend.auth_url(state, _redirect_uri())
    response.set_cookie(
        _DRIVE_STATE_COOKIE, state, max_age=600, httponly=True,
        samesite="lax", secure=get_settings().environment == "production", path="/",
    )
    return {"connected": False, "authUrl": url, "backend": backend.name}


@router.get("/connectors/drive/callback")
async def drive_callback(request: Request, code: str = Query(...),
                         state: str = Query(default=""),
                         db: Session = Depends(get_session),
                         user: CurrentUser = Depends(get_current_user)) -> RedirectResponse:
    _guard()
    # §4 · validate the OAuth state against the cookie set at connect-time.
    # Defends against a forged callback binding an attacker's Drive to the
    # victim's session.
    expected = request.cookies.get(_DRIVE_STATE_COOKIE)
    if not expected or not state or not secrets.compare_digest(expected, state):
        log.warning("drive_callback: OAuth state mismatch (possible CSRF)")
        resp = RedirectResponse(url="/?connector=drive&error=state_mismatch", status_code=302)
        resp.delete_cookie(_DRIVE_STATE_COOKIE, path="/")
        return resp
    backend = drive_mod.get_backend()
    try:
        tok = await backend.exchange(code, _redirect_uri())
    except LinkPullError:
        return RedirectResponse(url="/?connector=drive&error=auth_failed", status_code=302)
    conn_repo.upsert(db, provider="drive", backend=backend.name,
                     access_token=tok.access_token, refresh_token=tok.refresh_token,
                     account_email=tok.account_email)
    db.commit()
    resp = RedirectResponse(url="/?connector=drive&connected=1", status_code=302)
    resp.delete_cookie(_DRIVE_STATE_COOKIE, path="/")
    return resp


@router.delete("/connectors/drive")
def drive_disconnect(db: Session = Depends(get_session),
                     user: CurrentUser = Depends(get_current_user)) -> dict:
    _guard()
    removed = conn_repo.disconnect(db, "drive")
    return {"disconnected": removed}


# ── browse ──────────────────────────────────────────────────────────────────
def _require_account(db: Session):
    acct = conn_repo.get(db, "drive")
    if acct is None:
        raise HTTPException(status_code=409, detail="Drive is not connected")
    return acct


@router.get("/connectors/drive/folders")
async def drive_folders(db: Session = Depends(get_session),
                        user: CurrentUser = Depends(get_current_user)) -> dict:
    _guard()
    acct = _require_account(db)
    backend = drive_mod.get_backend()
    try:
        folders = await backend.list_folders(acct)
    except LinkPullError as e:
        raise HTTPException(status_code=e.http_status, detail=str(e))
    return {"folders": [{"id": f.id, "name": f.name} for f in folders]}


@router.get("/connectors/drive/folders/{folder_id}/files")
async def drive_files(folder_id: str, db: Session = Depends(get_session),
                      user: CurrentUser = Depends(get_current_user)) -> dict:
    _guard()
    acct = _require_account(db)
    backend = drive_mod.get_backend()
    try:
        files = await backend.list_files(acct, folder_id)
    except LinkPullError as e:
        raise HTTPException(status_code=e.http_status, detail=str(e))
    return {"files": [{"id": f.id, "name": f.name, "mimeType": f.mime_type} for f in files]}


# ── sync ──────────────────────────────────────────────────────────────────
class SyncPayload(BaseModel):
    folderId: str
    # Default KEEP the original. Purging it the instant ingestion finishes broke
    # (a) the document viewer, (b) downstream vision extraction that runs after
    # chunking, and (c) re-open. Opt out (keepOriginal=false) only purges via the
    # smart-retention path. The file always remains in the user's Drive anyway.
    keepOriginal: bool = True


class SyncSummary(BaseModel):
    folder: str
    created: list[dict]
    skipped: list[str]
    errors: list[str]
    retained: bool


async def _sync_folder(db: Session, acct, backend, folder_id: str,
                       keep_original: bool, user: CurrentUser,
                       auto_snapshot: bool = True) -> SyncSummary:
    """Pull every file in a Drive folder → dedup by sha256 → store → enqueue
    ingest. Shared by the legacy folder sync and the docaiq_docs inbox."""
    tenant = get_current_tenant()
    settings = get_settings()

    try:
        files = await backend.list_files(acct, folder_id)
    except LinkPullError as e:
        raise HTTPException(status_code=e.http_status, detail=str(e))

    created: list[dict] = []
    skipped: list[str] = []
    errors: list[str] = []

    import hashlib
    import io as _io

    for f in files:
        # §5 · change-detection — skip the expensive re-download when we already
        # hold this Drive file at the same modifiedTime. Without this, autosync
        # re-downloads every file every cycle (Drive 429s + wasted bandwidth).
        if repo.source_unchanged(db, f.id, getattr(f, "modified_time", None)):
            skipped.append(f.name)
            continue
        try:
            pulled = await backend.fetch(acct, f.id)
        except LinkPullError as e:
            errors.append(f"{f.name}: {e}")
            continue
        # B7 · decrypt a DocAIQ-encrypted copy (e.g. our own backup that landed
        # in docaiq_docs); no-op for user-dropped plaintext. Hash + store the
        # DECRYPTED bytes so dedup matches the original and the index is plaintext.
        # Resilient: a single file that can't be decrypted (e.g. encrypted under a
        # prior account pk) must NOT abort the whole sync — recover if possible,
        # else skip it and keep going so every other file still imports.
        from app import backup_keycache, drive_crypto
        raw = pulled.body
        if drive_crypto.is_pw_encrypted(raw):
            # Hardened: file encrypted with the user's password key — decrypt
            # with the cached key (set on unlock). Locked → skip (re-pullable
            # once they unlock).
            pk_key = backup_keycache.get(tenant, get_current_owner_user_pk())
            if pk_key is None:
                errors.append(f"{pulled.filename}: encrypted — unlock with your backup password to import")
                continue
            try:
                body = drive_crypto.decrypt_blob_pw(raw, pk_key)
            except Exception:  # noqa: BLE001
                errors.append(f"{pulled.filename}: wrong backup password — skipped")
                continue
        elif drive_crypto.is_encrypted(raw):  # legacy server-JWT copy
            try:
                body = drive_crypto.decrypt_blob_recover(get_current_owner_user_pk(), raw)
            except Exception:  # noqa: BLE001
                errors.append(f"{pulled.filename}: encrypted with a key we can't recover — skipped")
                continue
        else:
            body = raw  # plaintext (the new default)
        if len(body) > settings.max_upload_bytes:
            errors.append(f"{pulled.filename}: exceeds upload cap")
            continue
        sha = hashlib.sha256(body).hexdigest()
        if repo.get_by_sha256(db, sha) is not None:
            skipped.append(pulled.filename)
            continue
        suffix = secrets.token_hex(8)
        s3_key = f"{tenant}/documents/{sha[:2]}/{sha}-{suffix}"
        try:
            from app import storage
            storage.put_object(s3_key, _io.BytesIO(body), content_type=pulled.content_type)
        except Exception as e:  # noqa: BLE001
            errors.append(f"{pulled.filename}: storage write failed · {e}")
            continue
        row = repo.create_upload(
            db,
            # Random suffix → unique per upload across owners (see documents.py).
            id_external=f"doc-up-{sha[:10]}-{secrets.token_hex(3)}",
            name=pulled.filename,
            path=f"Google Drive › {f.id}",
            size=_human_size(len(pulled.body)),
            pages=1,
            mime_type=pulled.content_type or "application/octet-stream",
            sha256=sha,
            s3_key=s3_key,
            uploaded_by=user.email,
            source="drive",
            source_ref=f.id,
            retain_original=keep_original,
            modified=getattr(f, "modified_time", None) or "just now",  # enables change-detection next cycle
        )
        row.ingestion_status = "pending"
        db.commit()
        await enqueue_ingest(row.pk, tenant)
        created.append(repo._to_dict(row))  # noqa: SLF001

    # §5 · keep the Drive disaster-recovery snapshot fresh after any change, so
    # a future wipe can always be restored. Best-effort — never fail a sync on
    # the backup. Skipped during restore (auto_snapshot=False) so we don't
    # overwrite the good snapshot before its types are re-applied.
    if created and auto_snapshot:
        try:
            from app.services import workspace_export
            await workspace_export.sync_to_drive(
                db, tenant_id=tenant, owner_user_id=get_current_owner_user_pk())
        except Exception as e:  # noqa: BLE001
            log.warning("auto-snapshot after sync failed (non-fatal): %s", e)

    return SyncSummary(
        folder=folder_id,
        created=created, skipped=skipped, errors=errors,
        retained=keep_original,
    )


@router.post("/connectors/drive/sync", response_model=SyncSummary)
async def drive_sync(payload: SyncPayload, db: Session = Depends(get_session),
                     user: CurrentUser = Depends(get_current_user)) -> SyncSummary:
    """Sync a specific folder by id (advanced). The default flow is the
    docaiq_docs inbox below."""
    _guard()
    from app.services import subscriptions as subs
    subs.enforce_feature(db, owner_user_id=get_current_owner_user_pk(), feature="workspace")  # M47 · Drive folder-sync is Pro
    acct = _require_account(db)
    backend = drive_mod.get_backend()
    return await _sync_folder(db, acct, backend, payload.folderId, payload.keepOriginal, user)


# ── docaiq_docs inbox (the dedicated-folder model) ──────────────────────────
class InboxSyncPayload(BaseModel):
    keepOriginal: bool = True


@router.get("/connectors/drive/inbox")
async def drive_inbox(db: Session = Depends(get_session),
                      user: CurrentUser = Depends(get_current_user)) -> dict:
    """Find-or-create the dedicated `docaiq_docs` folder and list the files
    waiting in it. This is the only folder DocAIQ ever looks at."""
    _guard()
    acct = _require_account(db)
    backend = drive_mod.get_backend()
    try:
        fid = await backend.find_or_create_folder(acct, drive_mod.INBOX_FOLDER_NAME)
        files = await backend.list_files(acct, fid)
    except LinkPullError as e:
        raise HTTPException(status_code=e.http_status, detail=str(e))
    return {
        "folderName": drive_mod.INBOX_FOLDER_NAME,
        "folderId": fid,
        "count": len(files),
        "files": [{"id": f.id, "name": f.name, "mimeType": f.mime_type} for f in files],
    }


@router.post("/connectors/drive/sync-inbox", response_model=SyncSummary)
async def drive_sync_inbox(payload: InboxSyncPayload, db: Session = Depends(get_session),
                           user: CurrentUser = Depends(get_current_user)) -> SyncSummary:
    """Process everything currently in the docaiq_docs folder."""
    _guard()
    from app.services import subscriptions as subs
    subs.enforce_feature(db, owner_user_id=get_current_owner_user_pk(), feature="workspace")  # M47 · Drive folder-sync is Pro
    acct = _require_account(db)
    backend = drive_mod.get_backend()
    try:
        fid = await backend.find_or_create_folder(acct, drive_mod.INBOX_FOLDER_NAME)
    except LinkPullError as e:
        raise HTTPException(status_code=e.http_status, detail=str(e))
    return await _sync_folder(db, acct, backend, fid, payload.keepOriginal, user)



class BackupEncryptionPref(BaseModel):
    enabled: bool
    password: str | None = None


@router.post("/connectors/drive/backup-encryption")
async def set_backup_encryption(payload: BackupEncryptionPref,
                                db: Session = Depends(get_session),
                                user: CurrentUser = Depends(get_current_user)) -> dict:
    """Turn password encryption of the Drive backup on/off. Enabling requires a
    password (≥8 chars): we store ONLY a scrypt salt + a check token (never the
    password or key), cache the derived key for auto-backups, and re-write the
    backup encrypted. Disabling clears the salt/check and re-writes it plaintext."""
    _guard()
    _require_account(db)
    from app import backup_keycache, drive_crypto
    from app.orm import User
    from app.services import workspace_export
    tid = get_current_tenant()
    pk = get_current_owner_user_pk()
    u = db.get(User, pk)
    if payload.enabled:
        if not payload.password or len(payload.password) < 8:
            raise HTTPException(status_code=400, detail="A password of at least 8 characters is required.")
        salt = drive_crypto.new_salt()
        key = drive_crypto.derive_pw_key(payload.password, salt)
        u.backup_encryption = True
        u.backup_salt = salt
        u.backup_check = drive_crypto.make_check(key)
        db.commit()
        backup_keycache.put(tid, pk, key)
    else:
        u.backup_encryption = False
        u.backup_salt = None
        u.backup_check = None
        db.commit()
        backup_keycache.clear(tid, pk)
    # Re-write the backup in its new form so it's immediately consistent.
    try:
        await workspace_export.sync_to_drive(db, tenant_id=tid, owner_user_id=pk)
    except Exception as e:  # noqa: BLE001
        log.warning("backup re-write after encryption change failed (non-fatal): %s", e)
    return {"enabled": bool(u.backup_encryption)}


class BackupUnlock(BaseModel):
    password: str


@router.post("/connectors/drive/backup-unlock")
async def unlock_backup(payload: BackupUnlock,
                        db: Session = Depends(get_session),
                        user: CurrentUser = Depends(get_current_user)) -> dict:
    """Re-derive + cache the backup key from the user's password (verified
    against the stored check token) so encrypted auto-backups resume and the
    next restore can read the snapshot. Wrong password → 400."""
    _guard()
    from app import backup_keycache, drive_crypto
    from app.orm import User
    tid = get_current_tenant()
    pk = get_current_owner_user_pk()
    u = db.get(User, pk)
    if not u or not u.backup_encryption or not u.backup_salt:
        raise HTTPException(status_code=400, detail="Backup encryption is not enabled.")
    key = drive_crypto.derive_pw_key(payload.password, u.backup_salt)
    if not drive_crypto.verify_check(key, u.backup_check):
        raise HTTPException(status_code=400, detail="Incorrect password.")
    backup_keycache.put(tid, pk, key)
    return {"unlocked": True}


# ── back up direct uploads to Drive + free server space ─────────────────────
@router.post("/connectors/drive/backup-uploads")
async def backup_uploads(db: Session = Depends(get_session),
                         user: CurrentUser = Depends(get_current_user)) -> dict:
    """Copy the caller's direct uploads (still on the server) into their
    docaiq_docs Drive folder, then purge the server copies — freeing server
    space. Each becomes re-pullable from Drive on demand. Owner-scoped."""
    _guard()
    _require_account(db)  # 409 if Drive not connected
    from app.services import drive_backup
    docs = repo.list_for_backup(db)
    backed: list[str] = []
    errors: list[str] = []
    for doc in docs:
        try:
            if await drive_backup.backup_doc_to_drive(db, doc):
                backed.append(doc.name)
        except LinkPullError as e:
            errors.append(f"{doc.name}: {e}")
        except Exception as e:  # noqa: BLE001
            errors.append(f"{doc.name}: {e}")
    return {"backedUp": len(backed), "remaining": len(docs) - len(backed), "errors": errors}


# ── re-pull a purged original ────────────────────────────────────────────
@router.post("/documents/{doc_id}/repull")
async def repull_document(doc_id: str, db: Session = Depends(get_session),
                          user: CurrentUser = Depends(get_current_user)) -> dict:
    """Re-fetch a connector doc's original blob from source (after a retention
    purge) so it can be viewed/downloaded again. The chunks/embeddings already
    exist — this only re-materialises the file. Owner-scoped via repo.get_row."""
    _guard()
    row = repo.get_row(db, doc_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Document {doc_id} not found")
    if row.source != "drive" or not row.source_ref:
        raise HTTPException(status_code=400, detail="Document has no re-pull source")
    if row.s3_key:
        return repo._to_dict(row)  # noqa: SLF001 — original already present
    acct = _require_account(db)
    backend = drive_mod.get_backend()
    try:
        pulled = await backend.fetch(acct, row.source_ref)
    except LinkPullError as e:
        raise HTTPException(status_code=e.http_status, detail=str(e))

    import io as _io
    from app import drive_crypto, storage
    body = drive_crypto.decrypt_blob(row.owner_user_id, pulled.body)  # B7 · no-op for plaintext
    sha = row.sha256 or ""
    suffix = secrets.token_hex(8)
    s3_key = f"{get_current_tenant()}/documents/{sha[:2]}/{sha}-{suffix}"
    storage.put_object(s3_key, _io.BytesIO(body), content_type=pulled.content_type)
    row.s3_key = s3_key
    db.commit()
    return repo._to_dict(row)  # noqa: SLF001


# ── Google Picker import (bring in PRE-EXISTING Drive files within drive.file) ──
class DriveImportPayload(BaseModel):
    fileId: str
    keepOriginal: bool = True


@router.get("/connectors/drive/picker-config")
async def drive_picker_config(db: Session = Depends(get_session),
                              user: CurrentUser = Depends(get_current_user)) -> dict:
    """Config the frontend Google Picker needs: a fresh drive.file access token
    (minted from the connector's refresh token), the GCP app id, and the Picker
    API key. `enabled` is False when no Picker API key is set (button stays hidden)."""
    _guard()
    acct = _require_account(db)
    settings = get_settings()
    try:
        token = await drive_mod.get_backend()._access_token(acct)  # noqa: SLF001
    except LinkPullError as e:
        raise HTTPException(status_code=e.http_status, detail=str(e))
    cid = settings.google_client_id or ""
    app_id = cid.split("-", 1)[0] if "-" in cid else ""   # GCP project number
    api_key = settings.google_picker_api_key or ""
    return {"enabled": bool(api_key), "accessToken": token, "appId": app_id, "apiKey": api_key}


@router.post("/connectors/drive/import")
async def drive_import(payload: DriveImportPayload, db: Session = Depends(get_session),
                       user: CurrentUser = Depends(get_current_user)) -> dict:
    """Pull a Picker-selected Drive file (the user explicitly granted drive.file
    access to it) → store → ingest. Idempotent on sha256. Mirrors the folder-sync
    pull path for a single file."""
    import hashlib
    import io as _io

    from app import backup_keycache, drive_crypto, storage
    _guard()
    acct = _require_account(db)
    backend = drive_mod.get_backend()
    file_id = (payload.fileId or "").strip()
    if not file_id:
        raise HTTPException(status_code=400, detail="fileId is required")
    tenant = get_current_tenant()
    settings = get_settings()
    try:
        pulled = await backend.fetch(acct, file_id)
    except LinkPullError as e:
        raise HTTPException(status_code=e.http_status, detail=str(e))
    raw = pulled.body
    if drive_crypto.is_pw_encrypted(raw):
        pk_key = backup_keycache.get(tenant, get_current_owner_user_pk())
        if pk_key is None:
            raise HTTPException(status_code=409, detail="File is encrypted — unlock your backup password first")
        try:
            body = drive_crypto.decrypt_blob_pw(raw, pk_key)
        except Exception:  # noqa: BLE001
            raise HTTPException(status_code=400, detail="Wrong backup password for this file")
    elif drive_crypto.is_encrypted(raw):
        try:
            body = drive_crypto.decrypt_blob_recover(get_current_owner_user_pk(), raw)
        except Exception:  # noqa: BLE001
            raise HTTPException(status_code=400, detail="File encrypted with a key we can't recover")
    else:
        body = raw
    if len(body) > settings.max_upload_bytes:
        raise HTTPException(status_code=413, detail="File exceeds the upload size cap")
    sha = hashlib.sha256(body).hexdigest()
    existing = repo.get_by_sha256(db, sha)
    if existing is not None:
        return {"docId": existing.id_external, "name": existing.name, "status": "exists"}
    s3_key = f"{tenant}/documents/{sha[:2]}/{sha}-{secrets.token_hex(8)}"
    try:
        storage.put_object(s3_key, _io.BytesIO(body), content_type=pulled.content_type)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Storage write failed: {e}")
    try:
        row = repo.create_upload(
            db, id_external=f"doc-up-{sha[:10]}-{secrets.token_hex(3)}",
            name=pulled.filename, path=f"Google Drive (import) › {file_id}",
            size=_human_size(len(body)), pages=1,
            mime_type=pulled.content_type or "application/octet-stream", sha256=sha,
            s3_key=s3_key, uploaded_by=user.email, source="drive", source_ref=file_id,
            retain_original=payload.keepOriginal, modified="just now")
        row.ingestion_status = "pending"
        db.commit()
    except IntegrityError:
        # Concurrent import/upload of the same file+owner — return the winner's row.
        db.rollback()
        existing = repo.get_by_sha256(db, sha)
        if existing is not None:
            return {"docId": existing.id_external, "name": existing.name, "status": "exists"}
        raise
    await enqueue_ingest(row.pk, tenant)
    return {"docId": row.id_external, "name": row.name, "status": "ingested"}
