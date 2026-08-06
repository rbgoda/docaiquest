"""M44.P11.2 · PII-at-rest vault.

Reversible tokenization of PII inside the text we store in our OWN database.
The flow:

  ingest  → tokenize_document(text)  → returns text with `[CREDIT_CARD_1]`
            placeholders + persists the real values (Fernet-encrypted) into
            `pii_vault`, one row per (document, token).
  read    → detokenize(text)         → swaps placeholders back to real values,
            but ONLY when an authorized user revealed the doc (callers gate on
            doc.pii_protected AND doc.pii_revealed AND role).

Why a vault instead of irreversible scrubbing: a reviewer sometimes needs the
real passport / card number to verify a document. The vault keeps that possible
for authorized users while what we persist in chunks/extracted_fields shows only
placeholders.

Encryption key: derived from the tenant's own JWT secret (each tenant container
has a distinct one), so the vault is per-tenant-isolated with no extra secret to
manage. Lose the JWT secret → vault is unrecoverable (acceptable: same blast
radius as every other tenant secret).
"""
from __future__ import annotations

import base64
import hashlib
import logging
import re

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.orm import PIIVaultEntry
from app.pii import redact

log = logging.getLogger("docaiq.pii_vault")

_TOKEN_RE = re.compile(r"\[([A-Z_]+?)_\d+\]")


def _fernet():
    """Fernet cipher keyed off the tenant's JWT secret. Imported lazily so the
    module loads even where `cryptography` isn't needed."""
    from cryptography.fernet import Fernet

    secret = (get_settings().jwt_secret or "docaiq-dev-insecure").encode("utf-8")
    key = base64.urlsafe_b64encode(hashlib.sha256(secret + b"docaiq-pii-vault").digest())
    return Fernet(key)


def _kind_from_token(token: str) -> str:
    """`[CREDIT_CARD_1]` → `credit_card`."""
    m = _TOKEN_RE.match(token)
    return m.group(1).lower() if m else "unknown"


def tokenize_document(
    db: Session, *, tenant_id: str, document_pk: int, text: str
) -> str:
    """Replace PII in `text` with stable placeholders and persist the real
    values (encrypted) to the vault for this document. Returns the tokenized
    text. Idempotent per document: clears any prior vault rows first so a
    re-ingest doesn't accumulate duplicates.

    The mapping is computed ONCE for the whole document so the same value gets
    the same token everywhere (across chunks + extracted fields)."""
    r = redact(text)
    if not r.mapping:
        return text  # no PII detected — nothing to vault

    # Reset this doc's vault rows (re-ingest is idempotent).
    db.execute(delete(PIIVaultEntry).where(PIIVaultEntry.document_pk == document_pk))

    f = _fernet()
    for token, value in r.mapping.items():
        db.add(PIIVaultEntry(
            tenant_id=tenant_id,
            document_pk=document_pk,
            token=token,
            kind=_kind_from_token(token),
            value_encrypted=f.encrypt(value.encode("utf-8")).decode("ascii"),
        ))
    db.flush()
    return r.text


def apply_mapping(text: str, value_to_token: dict[str, str]) -> str:
    """Replace each real value with its token in an arbitrary string. Used to
    tokenize already-chunked text + extracted-field values with the document's
    canonical mapping. Longest values first so substrings don't clobber."""
    if not text or not value_to_token:
        return text
    for value in sorted(value_to_token, key=len, reverse=True):
        if value:
            text = text.replace(value, value_to_token[value])
    return text


def load_mapping(db: Session, document_pk: int) -> dict[str, str]:
    """token → real value for a document (decrypts the vault). Empty dict if
    the doc isn't protected or the key can't decrypt (fails closed)."""
    rows = db.scalars(
        select(PIIVaultEntry).where(PIIVaultEntry.document_pk == document_pk)
    ).all()
    if not rows:
        return {}
    f = _fernet()
    out: dict[str, str] = {}
    for row in rows:
        try:
            out[row.token] = f.decrypt(row.value_encrypted.encode("ascii")).decode("utf-8")
        except Exception as e:  # noqa: BLE001 — bad key / corrupt row · fail closed
            log.warning("pii_vault: decrypt failed for doc=%s token=%s: %s",
                        document_pk, row.token, e)
    return out


def detokenize(db: Session, document_pk: int, text: str) -> str:
    """Swap placeholders back to real values for an authorized reveal. Callers
    MUST gate on role + doc.pii_revealed before calling this."""
    if not text:
        return text
    mapping = load_mapping(db, document_pk)
    for token, value in mapping.items():
        text = text.replace(token, value)
    return text


def value_to_token_map(db: Session, document_pk: int) -> dict[str, str]:
    """Inverse of load_mapping (real value → token), for tokenizing chunks."""
    return {v: k for k, v in load_mapping(db, document_pk).items()}
