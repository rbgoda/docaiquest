"""Google Drive connector — pluggable backend.

Two implementations, selected by `settings.drive_backend`:

* **stub** (default in dev) — a deterministic in-memory fake: a fixed set of
  folders + small CSV files, no network and no credentials. Lets the whole
  connector flow (connect → list → sync → ingest → purge → re-pull) be tested
  locally end-to-end. "Connecting" is instant (no OAuth round-trip).

* **google** — the real thing: OAuth (drive.readonly, offline access for a
  refresh token) + Drive v3 list/download. Activates when
  `google_client_id`/`google_client_secret` are configured. Reuses the Drive v3
  endpoints from `app.link_pull`.

Mirrors the pluggable-backend pattern used by `app.embeddings` and the LLM
gateway. Backend methods are async so the `google` path can use httpx; the stub
just returns immediately.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx

from app.config import get_settings
from app.link_pull import PulledFile, LinkPullError

log = logging.getLogger("docaiq.connectors.drive")

# Drive v3 endpoints (shared with link_pull's public-folder path).
_DRIVE_FILES = "https://www.googleapis.com/drive/v3/files"
_TOKEN_URL = "https://oauth2.googleapis.com/token"
_AUTHZ_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_USERINFO = "https://www.googleapis.com/oauth2/v2/userinfo"
# M48 · public-launch scope policy. We request ONLY `drive.file` (+ identity),
# NOT `drive.readonly`. `drive.readonly` is a Google RESTRICTED scope whose
# public use requires the annual CASA security assessment ($$$, weeks) — a wall
# for a public launch. `drive.file` is non-restricted and grants access to files
# the app CREATES or the user explicitly OPENS with the app. That fully covers
# the core flow (uploads → app writes them into docaiq_docs → process → restore →
# workspace.sqlite), because those files are app-created.
#
# TRADE-OFF: with drive.file alone the app CANNOT read files a user manually
# drops into docaiq_docs (the app didn't create them). To let users bring in
# pre-existing Drive files post-launch, add a Google Picker flow (the user
# explicitly selects files → grants drive.file access to those). Tracked as a
# follow-up. Existing connections must reconnect once after this scope change.
_DRIVE_SCOPE = (
    "https://www.googleapis.com/auth/drive.file openid email"
)
# The one folder DocAIQ Documents looks at. Users drop files here; Sync ingests.
INBOX_FOLDER_NAME = "docaiq_docs"
# Drive's native Google-Docs types must be exported, not downloaded directly.
_EXPORT_MAP = {
    "application/vnd.google-apps.document": ("application/pdf", ".pdf"),
    "application/vnd.google-apps.spreadsheet": ("text/csv", ".csv"),
    "application/vnd.google-apps.presentation": ("application/pdf", ".pdf"),
}


@dataclass
class DriveFolder:
    id: str
    name: str


@dataclass
class DriveFile:
    id: str
    name: str
    mime_type: str
    # M47 · optional metadata (populated by the Google backend's list_files;
    # None on the stub). Used by the restore prompt to show "last saved".
    modified_time: str | None = None
    size: int | None = None


@dataclass
class DriveToken:
    """What a successful connect yields. For the stub these are sentinels."""
    access_token: str | None
    refresh_token: str | None
    account_email: str | None


# ── stub backend ───────────────────────────────────────────────────────────
_STUB_FILES: dict[str, tuple[str, str, bytes]] = {
    "stub-inv-001": ("Invoice_Q1.csv", "text/csv", b"vendor,amount\nAcme Corp,1200\nGlobex,840\n"),
    "stub-inv-002": ("Invoice_Q2.csv", "text/csv", b"vendor,amount\nInitech,640\nUmbrella,2200\n"),
    "stub-pol-001": ("Security_Policy.csv", "text/csv", b"control,status\nMFA,enabled\nEncryption,AES-256\n"),
    "stub-pol-002": ("Access_Review.csv", "text/csv", b"user,role\nalice,admin\nbob,viewer\n"),
}
_STUB_FOLDERS: dict[str, tuple[str, list[str]]] = {
    "stub-folder-docaiq": (INBOX_FOLDER_NAME, ["stub-inv-001", "stub-pol-001"]),
    "stub-folder-invoices": ("Invoices", ["stub-inv-001", "stub-inv-002"]),
    "stub-folder-policies": ("Policies", ["stub-pol-001", "stub-pol-002"]),
}


class StubDriveBackend:
    name = "stub"
    requires_oauth = False

    def auth_url(self, state: str, redirect_uri: str) -> str | None:
        # No OAuth — connecting is instant. Returning None signals the router
        # to mint a connected account directly.
        return None

    async def exchange(self, code: str, redirect_uri: str) -> DriveToken:
        return DriveToken(access_token="stub-access", refresh_token="stub-refresh",
                          account_email="you@stub.drive")

    def instant_token(self) -> DriveToken:
        return DriveToken(access_token="stub-access", refresh_token="stub-refresh",
                          account_email="you@stub.drive")

    async def find_or_create_folder(self, account, name: str, parent_id: str | None = None) -> str:
        for fid, (fname, _) in _STUB_FOLDERS.items():
            if fname == name:
                return fid
        return "stub-folder-docaiq"

    async def list_folders(self, account) -> list[DriveFolder]:
        return [DriveFolder(id=fid, name=name) for fid, (name, _) in _STUB_FOLDERS.items()]

    async def list_files(self, account, folder_id: str) -> list[DriveFile]:
        entry = _STUB_FOLDERS.get(folder_id)
        if not entry:
            return []
        _, file_ids = entry
        out = []
        for fid in file_ids:
            name, ct, _ = _STUB_FILES[fid]
            out.append(DriveFile(id=fid, name=name, mime_type=ct))
        return out

    async def fetch(self, account, file_id: str) -> PulledFile:
        entry = _STUB_FILES.get(file_id)
        if not entry:
            raise LinkPullError(f"stub file {file_id} not found", http_status=404)
        name, ct, body = entry
        return PulledFile(filename=name, body=body, content_type=ct)

    async def upload_file(self, account, name: str, data: bytes, mime: str, folder_id: str) -> str:
        fid = f"stub-up-{abs(hash(name)) % 10**8}"
        _STUB_FILES[fid] = (name, mime or "application/octet-stream", data)
        return fid

    async def share_folder(self, account, folder_id: str, email: str, role: str = "writer") -> None:
        return None  # stub — no real Drive permission to grant

    async def revoke_folder(self, account, folder_id: str, email: str) -> None:
        return None  # stub — no real Drive permission to revoke

    async def delete_file(self, account, file_id: str) -> None:
        return None  # stub — nothing to delete


# ── google backend ─────────────────────────────────────────────────────────
class GoogleDriveBackend:
    name = "google"
    requires_oauth = True

    def auth_url(self, state: str, redirect_uri: str) -> str | None:
        s = get_settings()
        params = {
            "client_id": s.google_client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": _DRIVE_SCOPE,
            "access_type": "offline",      # we need a refresh token
            "prompt": "consent",           # force refresh-token issuance
            "include_granted_scopes": "true",
            "state": state,
        }
        return f"{_AUTHZ_URL}?{urlencode(params)}"

    async def exchange(self, code: str, redirect_uri: str) -> DriveToken:
        s = get_settings()
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post(_TOKEN_URL, data={
                "code": code,
                "client_id": s.google_client_id,
                "client_secret": s.google_client_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            })
            if r.status_code != 200:
                raise LinkPullError(f"Drive token exchange failed: {r.status_code}", http_status=502)
            tok = r.json()
            email = None
            try:
                ui = await client.get(_USERINFO, headers={"Authorization": f"Bearer {tok['access_token']}"})
                if ui.status_code == 200:
                    email = ui.json().get("email")
            except httpx.HTTPError:
                pass
            return DriveToken(
                access_token=tok.get("access_token"),
                refresh_token=tok.get("refresh_token"),
                account_email=email,
            )

    async def _access_token(self, account) -> str:
        """Return a usable access token. Cached in Redis with a safe buffer below
        the token's own expiry so we don't hit Google's token endpoint on every
        Drive call (which 429s under fleet/autosync load). Fail-open: any Redis
        error just falls back to a fresh refresh."""
        s = get_settings()
        cache_key = f"docaiq:drivetok:{getattr(account, 'pk', None) or account.owner_user_id}"
        _r = None
        try:
            import redis as _redis
            _r = _redis.Redis.from_url(s.redis_url, decode_responses=True)
            cached = _r.get(cache_key)
            if cached:
                return cached
        except Exception as e:  # noqa: BLE001 — fail open: refresh below
            log.debug("drive token cache read skipped: %s", e)
            _r = None

        from app import drive_crypto
        refresh = drive_crypto.decrypt_token(account.owner_user_id, account.refresh_token)
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post(_TOKEN_URL, data={
                "client_id": s.google_client_id,
                "client_secret": s.google_client_secret,
                "refresh_token": refresh,
                "grant_type": "refresh_token",
            })
            if r.status_code != 200:
                # Distinguish a genuinely dead token from a transient Google
                # hiccup. Only `invalid_grant` (revoked / expired refresh token,
                # incl. the 7-day expiry on OAuth apps still in "Testing" status)
                # means the user must reconnect → 401. A 429/5xx is transient →
                # 503 so callers/retries don't wrongly force a reconnect.
                err = ""
                try:
                    err = (r.json() or {}).get("error", "")
                except Exception:  # noqa: BLE001
                    pass
                if err == "invalid_grant" or r.status_code in (400, 401, 403):
                    raise LinkPullError(
                        "Drive token expired — reconnect Google Drive", http_status=401)
                raise LinkPullError(
                    f"Drive token refresh temporarily unavailable ({r.status_code})",
                    http_status=503)
            j = r.json()
            access = j["access_token"]
            ttl = max(60, int(j.get("expires_in", 3600)) - 300)  # 5-min safety buffer
        if _r is not None:
            try:
                _r.setex(cache_key, ttl, access)
            except Exception as e:  # noqa: BLE001
                log.debug("drive token cache write skipped: %s", e)
        return access

    async def find_or_create_folder(self, account, name: str, parent_id: str | None = None) -> str:
        """Return the id of the user's `name` folder, creating it if missing.
        When `parent_id` is given, the folder is found/created INSIDE that parent.

        Find uses `drive.readonly`; create needs `drive.file` (added to the
        scope). A connection made before the scope change has read-only and
        will 403 on create — we surface a friendly "reconnect, or create the
        folder yourself" error instead of a raw 403.
        """
        token = await self._access_token(account)
        safe = name.replace("'", "\\'")
        q = (f"name='{safe}' and mimeType='application/vnd.google-apps.folder' and trashed=false")
        if parent_id:
            q += f" and '{parent_id}' in parents"
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(_DRIVE_FILES, params={
                "q": q, "fields": "files(id,name)", "pageSize": 10,
            }, headers={"Authorization": f"Bearer {token}"})
            if r.status_code != 200:
                raise LinkPullError(f"Drive folder lookup failed: {r.status_code}", http_status=502)
            hits = r.json().get("files", [])
            if hits:
                return hits[0]["id"]
            # Not found — create it (requires drive.file).
            body = {"name": name, "mimeType": "application/vnd.google-apps.folder"}
            if parent_id:
                body["parents"] = [parent_id]
            c = await client.post(_DRIVE_FILES, headers={"Authorization": f"Bearer {token}"},
                                  json=body)
            if c.status_code in (401, 403):
                raise LinkPullError(
                    f"Couldn't create the '{name}' folder. Reconnect Google Drive to grant "
                    f"folder access, or create a folder named '{name}' in your Drive yourself.",
                    http_status=409)
            if c.status_code not in (200, 201):
                raise LinkPullError(f"Drive folder create failed: {c.status_code}", http_status=502)
            return c.json()["id"]

    async def list_folders(self, account) -> list[DriveFolder]:
        token = await self._access_token(account)
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(_DRIVE_FILES, params={
                "q": "mimeType='application/vnd.google-apps.folder' and trashed=false",
                "fields": "files(id,name)",
                "pageSize": 200,
            }, headers={"Authorization": f"Bearer {token}"})
            if r.status_code != 200:
                raise LinkPullError(f"Drive folder list failed: {r.status_code}", http_status=502)
            return [DriveFolder(id=f["id"], name=f["name"]) for f in r.json().get("files", [])]

    async def list_files(self, account, folder_id: str) -> list[DriveFile]:
        token = await self._access_token(account)
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(_DRIVE_FILES, params={
                "q": f"'{folder_id}' in parents and trashed=false and mimeType!='application/vnd.google-apps.folder'",
                "fields": "files(id,name,mimeType,modifiedTime,size)",
                "pageSize": 200,
            }, headers={"Authorization": f"Bearer {token}"})
            if r.status_code != 200:
                raise LinkPullError(f"Drive file list failed: {r.status_code}", http_status=502)
            return [DriveFile(id=f["id"], name=f["name"], mime_type=f.get("mimeType", ""),
                              modified_time=f.get("modifiedTime"),
                              size=int(f["size"]) if f.get("size") else None)
                    for f in r.json().get("files", [])]

    async def fetch(self, account, file_id: str) -> PulledFile:
        token = await self._access_token(account)
        async with httpx.AsyncClient(timeout=60) as client:
            meta = await client.get(f"{_DRIVE_FILES}/{file_id}", params={"fields": "name,mimeType"},
                                    headers={"Authorization": f"Bearer {token}"})
            if meta.status_code != 200:
                raise LinkPullError(f"Drive metadata failed: {meta.status_code}", http_status=502)
            name = meta.json().get("name", file_id)
            mime = meta.json().get("mimeType", "")
            if mime in _EXPORT_MAP:
                export_mime, ext = _EXPORT_MAP[mime]
                resp = await client.get(f"{_DRIVE_FILES}/{file_id}/export",
                                        params={"mimeType": export_mime},
                                        headers={"Authorization": f"Bearer {token}"})
                if not name.endswith(ext):
                    name = f"{name}{ext}"
                content_type = export_mime
            else:
                resp = await client.get(f"{_DRIVE_FILES}/{file_id}",
                                        params={"alt": "media"},
                                        headers={"Authorization": f"Bearer {token}"})
                content_type = mime or "application/octet-stream"
            if resp.status_code != 200:
                raise LinkPullError(f"Drive download failed: {resp.status_code}", http_status=502)
            return PulledFile(filename=name, body=resp.content, content_type=content_type)

    async def upload_file(self, account, name: str, data: bytes, mime: str, folder_id: str) -> str:
        """Upload a file INTO the user's Drive (drive.file scope) under
        `folder_id`. Returns the new file id. Used to mirror a server-side
        upload to the user's docaiq_docs folder so the server copy can be
        purged (Drive becomes the store of record)."""
        import json as _json
        token = await self._access_token(account)
        meta = {"name": name, "parents": [folder_id]}
        boundary = "docaiq-boundary-7f3a1c"
        prelude = (
            f"--{boundary}\r\n"
            "Content-Type: application/json; charset=UTF-8\r\n\r\n"
            f"{_json.dumps(meta)}\r\n"
            f"--{boundary}\r\n"
            f"Content-Type: {mime or 'application/octet-stream'}\r\n\r\n"
        ).encode("utf-8")
        body = prelude + data + f"\r\n--{boundary}--".encode("utf-8")
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": f"multipart/related; boundary={boundary}",
        }
        url = "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart&fields=id"
        async with httpx.AsyncClient(timeout=120) as client:
            r = await client.post(url, content=body, headers=headers)
            if r.status_code in (401, 403):
                raise LinkPullError(
                    "Drive upload denied — reconnect Google Drive to grant write access.",
                    http_status=409)
            if r.status_code not in (200, 201):
                raise LinkPullError(f"Drive upload failed: {r.status_code}", http_status=502)
            return r.json()["id"]

    async def share_folder(self, account, folder_id: str, email: str, role: str = "writer") -> None:
        """Grant a member's Google account access to the group's shared folder
        (drive.file scope can manage permissions on app-created files). Sends no
        notification email. Best-effort — a 409 (already shared) is ignored."""
        token = await self._access_token(account)
        url = f"{_DRIVE_FILES}/{folder_id}/permissions?sendNotificationEmail=false"
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(url, headers={"Authorization": f"Bearer {token}"},
                                  json={"role": role, "type": "user", "emailAddress": email})
            if r.status_code in (200, 201, 204, 409):
                return
            raise LinkPullError(f"Drive folder share failed: {r.status_code}", http_status=502)

    async def revoke_folder(self, account, folder_id: str, email: str) -> None:
        """Remove a member's permission on the group's shared folder. Drive has
        no delete-by-email, so list the folder's permissions, find the one whose
        emailAddress matches, and DELETE it. Best-effort — a missing permission
        (already revoked) is a no-op."""
        token = await self._access_token(account)
        headers = {"Authorization": f"Bearer {token}"}
        target = (email or "").strip().lower()
        async with httpx.AsyncClient(timeout=30) as client:
            lr = await client.get(
                f"{_DRIVE_FILES}/{folder_id}/permissions",
                headers=headers,
                params={"fields": "permissions(id,emailAddress,type)"},
            )
            if lr.status_code != 200:
                raise LinkPullError(f"Drive list-permissions failed: {lr.status_code}", http_status=502)
            perms = (lr.json() or {}).get("permissions", [])
            pid = next((p.get("id") for p in perms
                        if (p.get("emailAddress") or "").strip().lower() == target), None)
            if not pid:
                return  # nothing to revoke
            dr = await client.delete(
                f"{_DRIVE_FILES}/{folder_id}/permissions/{pid}", headers=headers)
            if dr.status_code in (200, 204, 404):
                return
            raise LinkPullError(f"Drive revoke failed: {dr.status_code}", http_status=502)

    async def delete_file(self, account, file_id: str) -> None:
        """Delete a file the app created (e.g. a group-folder copy on unshare).
        A 404 (already gone) is a no-op."""
        token = await self._access_token(account)
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.delete(f"{_DRIVE_FILES}/{file_id}",
                                    headers={"Authorization": f"Bearer {token}"})
            if r.status_code in (200, 204, 404):
                return
            raise LinkPullError(f"Drive delete failed: {r.status_code}", http_status=502)


def get_backend():
    """Return the active Drive backend per settings.drive_backend. Falls back
    to stub if `google` is selected but creds are missing."""
    s = get_settings()
    if s.drive_backend == "google" and s.google_client_id and s.google_client_secret:
        return GoogleDriveBackend()
    return StubDriveBackend()
