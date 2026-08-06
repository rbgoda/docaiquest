"""M46 · canonical list of upload formats the ingestion pipeline supports.

Single source of truth for (a) the upload `accept` filter, (b) the "Supported
formats" hint in the Documents tab, and (c) what `storage.validate_upload`
accepts. Keep this in sync with `storage._ALLOWED_SNIFFED_MIMES` + the
extension tuples there. Legacy .doc/.xls (OLE) are intentionally NOT here —
they're rejected with a "re-save as .docx/.xlsx" message.
"""
from __future__ import annotations

SUPPORTED_UPLOAD_TYPES: list[dict] = [
    {"label": "PDF", "extensions": [".pdf"],
     "mimes": ["application/pdf"]},
    {"label": "Word", "extensions": [".docx"],
     "mimes": ["application/vnd.openxmlformats-officedocument.wordprocessingml.document"]},
    {"label": "Excel", "extensions": [".xlsx"],
     "mimes": ["application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"]},
    {"label": "CSV", "extensions": [".csv"],
     "mimes": ["text/csv"]},
    {"label": "Text", "extensions": [".txt", ".log", ".md", ".markdown"],
     "mimes": ["text/plain"]},
    {"label": "Email", "extensions": [".eml"],
     "mimes": ["message/rfc822"]},
    {"label": "Images", "extensions": [".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic", ".heif", ".avif"],
     "mimes": ["image/jpeg", "image/png", "image/webp", "image/gif", "image/heic", "image/heif", "image/avif"]},
]

UNSUPPORTED_NOTE = "Legacy .doc / .xls aren't supported — re-save as .docx / .xlsx and upload again."


def accept_attr() -> str:
    """The HTML <input accept="..."> value covering every supported type."""
    parts: list[str] = []
    for t in SUPPORTED_UPLOAD_TYPES:
        parts.extend(t["mimes"])
        parts.extend(t["extensions"])
    return ",".join(parts)
