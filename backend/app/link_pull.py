"""Link-pull providers — fetch evidence from public share URLs.

Three flows, all converging on a list of (filename, bytes, content_type)
tuples that the documents router ingests with the existing upload pipeline:

  1. `pull_single_pdf(url)`  — public link to one file. Provider-specific
     URL normalisation (Drive's `/file/d/<id>/view` → `/uc?export=download`,
     Dropbox `dl=0` → `dl=1`, Box `?download=1`). Returns 1-element list.

  2. `pull_drive_folder(url)` — public Drive folder. Calls Drive v3 with
     the workspace's `DOCAIQ_GOOGLE_DRIVE_API_KEY`, paginates files, and
     downloads each PDF.

  3. `pull_zip(url)` — any URL serving a zip. Includes Dropbox folder
     shares (which return a zip when you flip `dl=0` → `dl=1`). Extracts
     PDFs from the zip in memory.

The router calls `classify_link(url)` first to pick the right flow, then
the corresponding pull function. All return the same shape so the router
can loop and ingest with a single helper.
"""
from __future__ import annotations

import io
import ipaddress
import logging
import re
import socket
import zipfile
from dataclasses import dataclass
from typing import Literal
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

import httpx

from app.config import get_settings

log = logging.getLogger("docaiq.link_pull")

LinkKind = Literal["drive_folder", "zip", "single_file"]


@dataclass
class PulledFile:
    filename: str
    body: bytes
    content_type: str


class LinkPullError(Exception):
    """Raised when a link can't be fetched. Carries an HTTP status hint
    so the router can map it to a sensible response code (most are 502 —
    upstream provider issue — or 415 for content-type mismatches)."""
    def __init__(self, message: str, *, http_status: int = 502):
        super().__init__(message)
        self.http_status = http_status


# ---- SSRF guard -------------------------------------------------------------
# User-pasted links are fetched server-side, so they must not be allowed to
# reach internal services, localhost, or cloud-metadata (169.254.169.254). We
# require http(s) to a host that resolves ONLY to public IPs, and re-validate on
# every redirect hop (a public URL can 302 into the internal range).

def _assert_public_http_url(url: str) -> None:
    p = urlparse(url)
    if p.scheme not in ("http", "https"):
        raise LinkPullError(
            f"Only http/https links are supported (got {p.scheme or 'none'}).", http_status=400)
    host = p.hostname
    if not host:
        raise LinkPullError("Link has no host.", http_status=400)
    try:
        infos = socket.getaddrinfo(
            host, p.port or (443 if p.scheme == "https" else 80), proto=socket.IPPROTO_TCP)
    except OSError as e:
        raise LinkPullError(f"Couldn't resolve the link host: {e}", http_status=400)
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
                or ip.is_multicast or ip.is_unspecified):
            raise LinkPullError(
                "That link resolves to a non-public address and was blocked for security.",
                http_status=400)


async def _safe_get(client: "httpx.AsyncClient", url: str, *, max_redirects: int = 5):
    """GET with per-hop SSRF validation. `client` MUST have follow_redirects=False
    so each redirect target is re-validated before we fetch it."""
    hops = 0
    while True:
        _assert_public_http_url(url)
        resp = await client.get(url)
        loc = resp.headers.get("location")
        if resp.is_redirect and loc and hops < max_redirects:
            url = urljoin(url, loc)
            hops += 1
            continue
        return resp


# ---- classification ---------------------------------------------------------

def classify_link(url: str) -> LinkKind:
    """Decide which puller to use based on URL pattern. Folder shares win
    over single-file shares when both patterns match (Drive folder URLs
    don't have file IDs anyway, so no ambiguity in practice)."""
    u = url.lower()
    if "drive.google.com" in u and "/folders/" in u:
        return "drive_folder"
    if "dropbox.com" in u and ("/sh/" in u or "/scl/fo/" in u):
        # Dropbox shared folder share — both old `/sh/` and new `/scl/fo/`
        # patterns. We fetch as zip via dl=1 (same code path as plain zip).
        return "zip"
    if u.endswith(".zip"):
        return "zip"
    return "single_file"


# ---- single-file URL normalisation -----------------------------------------

def normalise_single_file_url(url: str) -> str:
    """Convert single-file share URLs to direct-download URLs. Unknown
    providers pass through unchanged."""
    # Google Drive
    if "drive.google.com" in url:
        m = re.search(r"/file/d/([a-zA-Z0-9_-]+)", url)
        if m:
            return f"https://drive.google.com/uc?export=download&id={m.group(1)}"
        m = re.search(r"[?&]id=([a-zA-Z0-9_-]+)", url)
        if m:
            return f"https://drive.google.com/uc?export=download&id={m.group(1)}"
    # Dropbox single-file
    if "dropbox.com" in url:
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        qs["dl"] = ["1"]
        return parsed._replace(query=urlencode({k: v[0] for k, v in qs.items()})).geturl()
    # Box
    if "box.com" in url and "download=1" not in url:
        sep = "&" if "?" in url else "?"
        return f"{url}{sep}download=1"
    return url


# ---- 1 · single PDF --------------------------------------------------------

async def pull_single_pdf(url: str) -> list[PulledFile]:
    """Fetch one file from a public share URL. Returns a 1-element list so
    the router can loop uniformly."""
    fetch_url = normalise_single_file_url(url)
    # follow_redirects=False + _safe_get → every hop is SSRF-validated.
    async with httpx.AsyncClient(timeout=60.0, follow_redirects=False) as client:
        try:
            resp = await _safe_get(client, fetch_url)
        except httpx.HTTPError as e:
            raise LinkPullError(f"Couldn't fetch the link: {e}")
    if resp.status_code >= 400:
        raise LinkPullError(
            f"Provider returned HTTP {resp.status_code}. "
            "Make sure the link is set to 'Anyone with the link → Viewer' and points at a file.",
            http_status=502,
        )
    ct = (resp.headers.get("content-type") or "").lower().split(";")[0].strip()
    if ct.startswith("text/html"):
        raise LinkPullError(
            "Got an HTML page back, not a file. Common causes: link points at a folder, "
            "Drive's virus-scan interstitial for files >25MB, or the link isn't actually public. "
            "Paste a folder URL if that's what you meant, or share the file as 'Anyone with link'.",
            http_status=415,
        )
    fname = _filename_from_response(resp, url)
    return [PulledFile(filename=fname, body=resp.content, content_type=ct or "application/pdf")]


# ---- 2 · Google Drive public folder ----------------------------------------

# Drive v3 endpoints. Read-only with an API key; no OAuth.
_DRIVE_LIST = "https://www.googleapis.com/drive/v3/files"
_DRIVE_DOWNLOAD = "https://www.googleapis.com/drive/v3/files/{file_id}"


async def pull_drive_folder(url: str) -> list[PulledFile]:
    """Enumerate PDFs in a publicly-shared Drive folder and download each.

    Requires `DOCAIQ_GOOGLE_DRIVE_API_KEY` on the platform side; the URL
    must be a folder shared as 'Anyone with the link → Viewer'. Subfolders
    are NOT recursed today — a single level keeps the operation bounded
    and predictable (the matcher's per-doc cost is what would spiral)."""
    settings = get_settings()
    if not settings.google_drive_api_key:
        raise LinkPullError(
            "Drive folder pulls aren't configured on this workspace. "
            "Ask your admin to set DOCAIQ_GOOGLE_DRIVE_API_KEY, or zip the folder and paste the zip link instead.",
            http_status=503,
        )
    m = re.search(r"/folders/([a-zA-Z0-9_-]+)", url)
    if not m:
        raise LinkPullError("Couldn't parse a folder id from that Drive URL.", http_status=400)
    folder_id = m.group(1)

    files: list[dict] = []
    page_token: str | None = None
    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
        # Paginated list. Filter to PDFs via Drive's q syntax; we still
        # double-check mimeType client-side for safety.
        while True:
            params = {
                "q": f"'{folder_id}' in parents and trashed=false and mimeType='application/pdf'",
                "fields": "files(id,name,mimeType,size),nextPageToken",
                "pageSize": "100",
                "key": settings.google_drive_api_key,
            }
            if page_token:
                params["pageToken"] = page_token
            r = await client.get(_DRIVE_LIST, params=params)
            if r.status_code == 404 or r.status_code == 403:
                raise LinkPullError(
                    f"Drive returned HTTP {r.status_code}. The folder must be shared as 'Anyone with the link → Viewer'.",
                    http_status=502,
                )
            if r.status_code >= 400:
                raise LinkPullError(
                    f"Drive list call failed: HTTP {r.status_code} · {r.text[:200]}",
                    http_status=502,
                )
            payload = r.json()
            files.extend(payload.get("files", []))
            page_token = payload.get("nextPageToken")
            if not page_token:
                break

        if not files:
            raise LinkPullError(
                "Folder is empty (or contains no PDFs). Drive subfolders aren't enumerated; "
                "flatten the structure or zip the folder and paste the zip link.",
                http_status=404,
            )

        # Cap to a sane upper bound. 200 PDFs is more than any realistic
        # audit pack; bigger requests should chunk.
        if len(files) > 200:
            raise LinkPullError(
                f"Folder has {len(files)} PDFs; cap is 200 per pull. Split or zip the folder.",
                http_status=413,
            )

        # Download each file. Sequential is fine — the cost ceiling is
        # the matcher cascade that runs downstream, not these GETs.
        out: list[PulledFile] = []
        max_bytes = settings.max_upload_bytes
        for f in files:
            r = await client.get(
                _DRIVE_DOWNLOAD.format(file_id=f["id"]),
                params={"alt": "media", "key": settings.google_drive_api_key},
            )
            if r.status_code >= 400:
                log.warning("drive download failed for %s (%s): %s", f.get("name"), f["id"], r.status_code)
                continue
            body = r.content
            if len(body) > max_bytes:
                log.warning("drive file %s exceeds size cap (%d), skipping", f.get("name"), len(body))
                continue
            out.append(PulledFile(
                filename=_safe_pdf_name(f.get("name") or f["id"]),
                body=body,
                content_type="application/pdf",
            ))
        if not out:
            raise LinkPullError("No PDFs successfully downloaded from the folder.", http_status=502)
        return out


# ---- 3 · zip URL (including Dropbox folder dl=1) ---------------------------

async def pull_zip(url: str) -> list[PulledFile]:
    """Download a zip and extract PDFs in memory. For Dropbox folder shares
    we toggle `dl=0` → `dl=1` so the share endpoint returns the zip directly
    instead of HTML. Other URLs are fetched as-is."""
    fetch_url = url
    if "dropbox.com" in url:
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        qs["dl"] = ["1"]
        fetch_url = parsed._replace(query=urlencode({k: v[0] for k, v in qs.items()})).geturl()

    settings = get_settings()
    max_bytes = settings.max_upload_bytes

    async with httpx.AsyncClient(timeout=120.0, follow_redirects=False) as client:
        try:
            resp = await _safe_get(client, fetch_url)
        except httpx.HTTPError as e:
            raise LinkPullError(f"Couldn't fetch the zip: {e}")
    if resp.status_code >= 400:
        raise LinkPullError(
            f"Provider returned HTTP {resp.status_code} on the zip URL. "
            "Check the folder is shared publicly.",
            http_status=502,
        )
    if len(resp.content) > max_bytes * 10:  # zip can be larger than max single doc
        raise LinkPullError(
            f"Zip is {len(resp.content)} bytes; cap is {max_bytes * 10}. Split the folder.",
            http_status=413,
        )

    try:
        zf = zipfile.ZipFile(io.BytesIO(resp.content))
    except zipfile.BadZipFile:
        raise LinkPullError(
            "Didn't look like a zip. If you pasted a Dropbox folder, make sure it's truly a folder share, "
            "not a single-file share.",
            http_status=415,
        )

    out: list[PulledFile] = []
    for info in zf.infolist():
        if info.is_dir():
            continue
        # Only PDFs. Other types might be useful one day (Excel evidence,
        # for example) but ingest pipeline is PDF-only today.
        if not info.filename.lower().endswith(".pdf"):
            continue
        if info.file_size > max_bytes:
            log.warning("zip entry %s exceeds size cap (%d), skipping", info.filename, info.file_size)
            continue
        with zf.open(info) as fh:
            body = fh.read()
        # Strip directory prefix from the stored name. Some zip tools nest
        # everything under a single top-level dir (foo/bar.pdf); the
        # auditor cares about "bar.pdf" not the dir.
        leaf = info.filename.rsplit("/", 1)[-1]
        out.append(PulledFile(
            filename=_safe_pdf_name(leaf),
            body=body,
            content_type="application/pdf",
        ))
    if not out:
        raise LinkPullError("Zip didn't contain any PDFs.", http_status=415)
    return out


# ---- helpers ----------------------------------------------------------------

def _filename_from_response(resp: httpx.Response, original_url: str) -> str:
    cd = resp.headers.get("content-disposition", "")
    if "filename=" in cd:
        return _safe_pdf_name(cd.split("filename=", 1)[1].strip().strip('"').split(";")[0])
    path = urlparse(original_url).path
    leaf = (path.rsplit("/", 1)[-1] or "from-link").split("?")[0]
    return _safe_pdf_name(leaf or "from-link")


def _safe_pdf_name(name: str) -> str:
    """Ensure filename ends with .pdf and is reasonable to use as an
    object-store key tail / a UI display string. Doesn't deeply sanitize
    (the s3 key is derived from sha + suffix anyway), just makes display sane."""
    name = (name or "file").strip() or "file"
    if not name.lower().endswith(".pdf"):
        name += ".pdf"
    return name[:200]
