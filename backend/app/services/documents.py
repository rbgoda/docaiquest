"""Documents service — helpers extracted from `routers/documents.py` (TODO #25).

Pure functions: take db + plain args, return data or perform a focused
side effect. No HTTP concerns. The router stays in charge of
auth / status codes; the service owns the business operations that
multiple endpoints share.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_current_tenant
from app.orm import Requirement


def link_doc_to_requirement(
    db: Session, doc_id_external: str, req_id_external: str
) -> None:
    """Force-link a document to a requirement. Tenant-scoped. Silent no-op if
    the requirement doesn't exist (avoids breaking the upload over a stale
    id sent by the frontend). The matcher honors an existing link and will
    not overwrite it on subsequent matches — see agents/matcher.py."""
    tid = get_current_tenant()
    req = db.scalar(
        select(Requirement).where(
            Requirement.tenant_id == tid,
            Requirement.id_external == req_id_external,
        )
    )
    if req is None:
        return
    req.doc_id_external = doc_id_external


def human_size(n: int) -> str:
    """Human-readable byte size — '1.2 MB' etc. Used in upload-side display
    strings (Document.size column, link-pull error messages)."""
    size: float = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"
