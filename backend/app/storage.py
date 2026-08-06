"""S3-compatible object storage. Today MinIO in compose; in production the
same boto3 client targets AWS S3 / Azure Blob (via S3-compat) / GCS (via
HMAC interop) by swapping endpoint + credentials.

We deliberately don't expose MinIO directly to the browser. All uploads and
downloads stream through the backend — that side-steps CORS, per-tenant port
mapping, and presigned-URL host rewriting. The throughput hit is negligible
for an audit platform serving PDFs occasionally.
"""

from __future__ import annotations

import hashlib
import io
import logging
from functools import lru_cache
from typing import BinaryIO, Iterator

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from app.config import get_settings

log = logging.getLogger("docaiq.storage")


@lru_cache
def _client():
    s = get_settings()
    return boto3.client(
        "s3",
        endpoint_url=s.s3_endpoint,
        aws_access_key_id=s.s3_access_key,
        aws_secret_access_key=s.s3_secret_key,
        region_name=s.s3_region,
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


@lru_cache
def _bucket() -> str:
    return get_settings().s3_bucket


def ensure_bucket() -> None:
    """Idempotent bucket creation. Runs on first upload. Cheaper than a
    startup hook because we don't pay the round-trip until we actually need
    storage."""
    bucket = _bucket()
    client = _client()
    try:
        client.head_bucket(Bucket=bucket)
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code")
        if code in {"404", "NoSuchBucket", "NotFound"}:
            log.info("Creating bucket %s", bucket)
            client.create_bucket(Bucket=bucket)
        else:
            raise


def put_object(key: str, body: BinaryIO, content_type: str | None) -> None:
    ensure_bucket()
    extra: dict = {}
    if content_type:
        extra["ContentType"] = content_type
    _client().put_object(Bucket=_bucket(), Key=key, Body=body, **extra)


def stream_object(key: str, chunk_size: int = 64 * 1024) -> Iterator[bytes]:
    """Stream a stored object in chunks. The router wraps this in a
    StreamingResponse so we never buffer the whole file in memory."""
    obj = _client().get_object(Bucket=_bucket(), Key=key)
    body = obj["Body"]
    try:
        while True:
            chunk = body.read(chunk_size)
            if not chunk:
                break
            yield chunk
    finally:
        body.close()


def head_object(key: str) -> dict | None:
    try:
        return _client().head_object(Bucket=_bucket(), Key=key)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
            return None
        raise


def delete_object(key: str) -> None:
    _client().delete_object(Bucket=_bucket(), Key=key)


def get_object_bytes(key: str) -> bytes | None:
    """Convenience reader for small objects (≤ a few MB) — e.g. KYC ID
    images that need to be base64-encoded into a vision API request.
    Returns None if the object doesn't exist."""
    try:
        obj = _client().get_object(Bucket=_bucket(), Key=key)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
            return None
        raise
    return obj["Body"].read()


def hash_and_buffer(fileobj: BinaryIO, max_bytes: int) -> tuple[bytes, str]:
    """Read `fileobj` fully into memory while computing sha256.
    Returns (raw_bytes, hex_digest). Raises ValueError if size > max_bytes.

    We buffer in memory for now because tenant-uploaded compliance docs are
    typically < 50 MB. If we ever need to handle GB-scale ingest (M7+), this
    becomes a temp-file pipeline."""
    buf = io.BytesIO()
    sha = hashlib.sha256()
    read = 0
    while True:
        chunk = fileobj.read(1024 * 1024)
        if not chunk:
            break
        read += len(chunk)
        if read > max_bytes:
            raise ValueError(f"Upload exceeds {max_bytes} bytes")
        sha.update(chunk)
        buf.write(chunk)
    return buf.getvalue(), sha.hexdigest()


# ── Content-type sniffing + filename sanitization (TODO #15) ──────────────

# What we accept. Anything outside this list is refused at the upload
# boundary regardless of what the client declared. CSV is a special case
# (not a magic-byte format); we accept it via extension + the explicit
# text/csv MIME if and only if the body is ASCII-clean.
_ALLOWED_SNIFFED_MIMES = {
    "application/pdf",
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/gif",
    "image/heic",
    "image/heif",
    "image/avif",
    # T1.2 · Office docs. Both OOXML (.docx/.xlsx zip-based) and legacy OLE.
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",  # .docx
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",        # .xlsx
    "text/plain",                                                                # .txt / .md
    "message/rfc822",                                                            # .eml
    # Legacy OLE formats (.doc/.xls) intentionally excluded — needs
    # libreoffice subprocess conversion. Users should re-save as .docx/.xlsx.
}

# Magic-byte → canonical MIME. Hand-rolled so we don't depend on libmagic
# in the runtime image. The common upload formats below cover ~99% of
# real audit-doc traffic; anything else (Word, Excel, etc.) gets refused
# explicitly — we don't yet parse them, so accepting them is dishonest.
def sniff_mime(raw: bytes) -> str | None:
    head = raw[:32]
    if head.startswith(b"%PDF-"):
        return "application/pdf"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    # ISO BMFF / HEIC / HEIF / AVIF — first 4 bytes are box size, then "ftyp",
    # then the brand identifies which container.
    if len(head) >= 12 and head[4:8] == b"ftyp":
        brand = bytes(head[8:12])
        if brand in (b"heic", b"heix", b"mif1", b"msf1", b"heim", b"heis"):
            return "image/heic"
        if brand in (b"avif", b"avis"):
            return "image/avif"
    # T1.2 · OOXML (.docx/.xlsx/.pptx) — ZIP container; distinguish by
    # extension since we only have the head 32 bytes. Caller routes by
    # filename. Legacy OLE2 (.doc/.xls) starts with D0CF11E0A1B11AE1.
    if head[:4] == b"PK\x03\x04":
        # Generic zip · let extension drive routing in validate_upload.
        return "application/zip"
    if head[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        # OLE Compound · could be .doc or .xls; extension decides.
        return "application/x-ole-storage"
    return None


# Allowlist of upload file extensions for formats without magic bytes (CSV,
# plain text) or for routing zip/OLE archives to their specific MIME.
_CSV_EXTENSIONS = (".csv",)
_TEXT_EXTENSIONS = (".txt", ".log", ".md", ".markdown")
_EML_EXTENSIONS = (".eml",)
# T1.2 · OOXML extensions — used to route a zip body to the right MIME.
_DOCX_EXTENSIONS = (".docx",)
_XLSX_EXTENSIONS = (".xlsx",)
_PPTX_EXTENSIONS = (".pptx",)
# Formats handled via LibreOffice → PDF when office_convert_enabled. ODF are zip
# containers; RTF is text-ish; legacy .doc/.xls/.ppt are OLE. Gated in validate_upload.
_ODF_EXTENSIONS = (".odt", ".ods", ".odp")
_OLE_CONVERT_EXTENSIONS = (".doc", ".xls", ".ppt")
_RTF_EXTENSIONS = (".rtf",)


def validate_upload(raw: bytes, declared_mime: str | None, filename: str | None) -> str:
    """Return the AUTHORITATIVE MIME for the uploaded bytes, or raise
    ValueError if the upload should be rejected.

    Trust order:
      1. Magic-byte sniff (most authoritative)
      2. Extension-based for formats without distinctive magic bytes
         (CSV/text/email) OR for routing generic containers to a specific
         MIME (zip → docx/xlsx, OLE → doc/xls)
      3. Refuse — declared MIME from the client is NEVER trusted on its own
    """
    sniffed = sniff_mime(raw)
    name = (filename or "").lower()

    office = get_settings().office_convert_enabled

    # T1.2 · route OOXML/ODF zips to their specific MIME by extension.
    if sniffed == "application/zip":
        if name.endswith(_DOCX_EXTENSIONS):
            return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        if name.endswith(_XLSX_EXTENSIONS):
            return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        if name.endswith(_PPTX_EXTENSIONS):  # python-pptx — always supported
            return "application/vnd.openxmlformats-officedocument.presentationml.presentation"
        if name.endswith(_ODF_EXTENSIONS) and office:  # LibreOffice convert (opt-in)
            return "application/vnd.oasis.opendocument"
        # Unknown zip — refuse.
        raise ValueError(
            f"Unsupported zip-based file (filename={name!r}). "
            "Allowed: .docx, .xlsx, .pptx" + (", .odt/.ods/.odp" if office else "") + "."
        )
    # Legacy OLE (.doc/.xls/.ppt) — convert via LibreOffice when enabled, else reject.
    if sniffed == "application/x-ole-storage":
        if name.endswith(_OLE_CONVERT_EXTENSIONS) and office:
            return "application/x-ole-storage"
        raise ValueError(
            "Legacy .doc/.xls/.ppt not supported in this deployment. "
            "Re-save as .docx/.xlsx/.pptx" + ("" if office else " (or enable office conversion)") + "."
        )
    # RTF — text-based; convert via LibreOffice when enabled.
    if name.endswith(_RTF_EXTENSIONS) and office and raw[:5] == b"{\\rtf":
        return "application/rtf"

    if sniffed and sniffed in _ALLOWED_SNIFFED_MIMES:
        return sniffed
    # CSV fallback. Body must be ASCII-only (no leading PE/ELF/MZ/etc.).
    if name.endswith(_CSV_EXTENSIONS):
        try:
            raw[:8192].decode("ascii")
            return "text/csv"
        except UnicodeDecodeError:
            pass
    # T1.2 · Plain text. Decode-safe check.
    if name.endswith(_TEXT_EXTENSIONS):
        try:
            raw[:16384].decode("utf-8")
            return "text/plain"
        except UnicodeDecodeError:
            try:
                raw[:16384].decode("latin-1")
                return "text/plain"
            except UnicodeDecodeError:
                pass
    # T1.2 · Email — RFC 5322 / 822. EML files are plain text with headers.
    if name.endswith(_EML_EXTENSIONS):
        try:
            head_text = raw[:4096].decode("utf-8", errors="replace")
            # Must look like an email — at least one header line in the head.
            if ":" in head_text.split("\n")[0]:
                return "message/rfc822"
        except UnicodeDecodeError:
            pass
    raise ValueError(
        f"Unsupported file type (declared={declared_mime!r}, sniffed={sniffed!r}). "
        "Allowed: PDF, PNG, JPEG, WebP, GIF, HEIC, AVIF, CSV, DOCX, XLSX, DOC, XLS, TXT, MD, EML."
    )


def sanitize_filename(raw: str | None, *, fallback: str = "upload.bin") -> str:
    """Return a Content-Disposition-safe filename. Strips path components,
    control characters, header-injection chars, and length-caps to 200.
    Never returns an empty string."""
    if not raw:
        return fallback
    # Drop any directory portion (Windows + POSIX).
    name = raw.replace("\\", "/").rsplit("/", 1)[-1]
    # Strip control chars and quote chars that could break the
    # Content-Disposition header (" \r \n ; ).
    name = "".join(ch for ch in name if ch.isprintable() and ch not in '"\r\n;')
    name = name.strip(" .")  # leading/trailing dots + spaces are footguns on Windows
    if not name:
        return fallback
    return name[:200]
