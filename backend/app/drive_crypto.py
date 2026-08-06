"""M46 · B7 · client-side encryption of Drive-stored files.

Files we push to a user's Google Drive are encrypted FIRST, so neither Google
nor anyone reading the raw Drive bytes can see the content — only DocAIQ (via the
app) can decrypt. The key is **server-escrowed**: deterministically derived from
the tenant's JWT secret + the owner's user id, so the server can always recover it
(no data loss when the toggle flips), but it is per-user, not a single global key.

Self-describing: encrypted blobs carry a magic header, so `decrypt_blob` is a safe
no-op on plaintext (e.g. files the user dropped into docaiq_docs themselves). That
lets encryption be toggled on/off without breaking access to existing files.

Toggle: settings.documents_drive_encryption. When OFF, new uploads go up
plaintext; already-encrypted files still decrypt on the way back.
"""
from __future__ import annotations

import base64
import hashlib
import logging

from app.config import get_settings

log = logging.getLogger("docaiq.drive_crypto")

# Magic prefix marking a DocAIQ-encrypted Drive blob (v1).
_MAGIC = b"DQDRENC1\n"


def _fernet(owner_user_id: int | None):
    from cryptography.fernet import Fernet
    secret = (get_settings().jwt_secret or "docaiq-dev-insecure").encode("utf-8")
    material = secret + b"docaiq-drive-enc:" + str(owner_user_id or 0).encode("ascii")
    key = base64.urlsafe_b64encode(hashlib.sha256(material).digest())
    return Fernet(key)


def is_encrypted(data: bytes) -> bool:
    return bool(data) and data[:len(_MAGIC)] == _MAGIC


# ── M50 · string-safe token encryption (OAuth access/refresh tokens) ─────────
# Tokens live in a String column, so we keep the ciphertext as an ASCII string
# (Fernet output is urlsafe-b64) behind a text magic prefix. SELF-DESCRIBING +
# BACKWARD-COMPATIBLE: a legacy plaintext token (no prefix) decrypts to a no-op,
# so existing rows keep working until the migration (or next write) encrypts them.
_TOKEN_MAGIC = "DQTOK1:"


def encrypt_token(owner_user_id: int | None, token: str | None) -> str | None:
    """Encrypt an OAuth token for at-rest storage. No-op on empty / already-encrypted.
    Never raises — a crypto failure stores plaintext rather than blocking a connect."""
    if not token or token.startswith(_TOKEN_MAGIC):
        return token
    try:
        ct = _fernet(owner_user_id).encrypt(token.encode("utf-8")).decode("ascii")
        return _TOKEN_MAGIC + ct
    except Exception as e:  # noqa: BLE001
        log.warning("drive_crypto: token encrypt failed for owner=%s: %s", owner_user_id, e)
        return token


def decrypt_token(owner_user_id: int | None, token: str | None) -> str | None:
    """Decrypt a stored OAuth token. No-op for legacy plaintext (no magic prefix)."""
    if not token or not token.startswith(_TOKEN_MAGIC):
        return token
    try:
        return _fernet(owner_user_id).decrypt(token[len(_TOKEN_MAGIC):].encode("ascii")).decode("utf-8")
    except Exception as e:  # noqa: BLE001
        log.warning("drive_crypto: token decrypt failed for owner=%s: %s", owner_user_id, e)
        return token


def encrypt_blob(owner_user_id: int | None, data: bytes, *, enabled: bool) -> bytes:
    """Encrypt `data` for storage in the owner's Drive when `enabled` (the user's
    per-account encrypt_files choice). No-op if disabled or already encrypted.
    Returns magic-prefixed ciphertext."""
    if not data or not enabled or is_encrypted(data):
        return data
    try:
        token = _fernet(owner_user_id).encrypt(data)
        return _MAGIC + token
    except Exception as e:  # noqa: BLE001 — never block an upload on crypto
        log.warning("drive_crypto: encrypt failed (uploading plaintext): %s", e)
        return data


def decrypt_blob(owner_user_id: int | None, data: bytes) -> bytes:
    """Decrypt a blob fetched from Drive. Safe no-op when the blob isn't
    DocAIQ-encrypted (plaintext / user-dropped files)."""
    if not is_encrypted(data):
        return data
    try:
        return _fernet(owner_user_id).decrypt(data[len(_MAGIC):])
    except Exception as e:  # noqa: BLE001 — fail closed: don't hand back ciphertext as if plaintext
        log.warning("drive_crypto: decrypt failed for owner=%s: %s", owner_user_id, e)
        raise


# Defensive cap on how many prior-pk candidates we'll try per blob.
_MAX_RECOVER_CANDIDATES = 25


def decrypt_blob_recover(owner_user_id: int | None, data: bytes,
                         candidate_ids: list[int] | None = None) -> bytes:
    """Like decrypt_blob, but if the per-user key doesn't match (e.g. the file
    was encrypted under a previous account pk that has since been recreated with
    a new pk), also try the owner's EXPLICIT prior pks in `candidate_ids`.

    Security: this no longer brute-forces the global pk space (the old
    `range(1, 201)` default). That tried to decrypt a blob under *any* of the
    first 200 users' keys — a CPU-amplification vector (200 KDF+AEAD ops per bad
    blob) and a cross-account footgun. Callers must pass the current owner's own
    known prior pks; with no candidates this is just `decrypt_blob`. Raises if
    nothing matches (caller skips that file)."""
    if not is_encrypted(data):
        return data
    from cryptography.fernet import InvalidToken
    try:
        return _fernet(owner_user_id).decrypt(data[len(_MAGIC):])
    except Exception:  # noqa: BLE001 — fall through to explicit candidate recovery
        pass
    tried = {owner_user_id}
    for cid in (candidate_ids or [])[:_MAX_RECOVER_CANDIDATES]:
        if cid in tried:
            continue
        tried.add(cid)
        try:
            out = _fernet(cid).decrypt(data[len(_MAGIC):])
            log.warning("drive_crypto: recovered blob with prior owner pk=%s (current=%s)", cid, owner_user_id)
            return out
        except Exception:  # noqa: BLE001
            continue
    raise InvalidToken("no candidate key matched")


# ── Optional password-based backup encryption (user-owned key) ───────────────
# The backup is encrypted with a scrypt key derived from the USER's password.
# The server stores only the salt + a check token — never the password or key,
# so a lost password means the backup is unrecoverable (the user's Drive
# originals remain re-importable). Distinct magic prefix so restore can tell a
# password-encrypted backup from plaintext or the legacy JWT-secret one.
_PW_MAGIC = b"DQDRPW1\n"
_CHECK_SENTINEL = b"docaiq-backup-ok"


def new_salt() -> str:
    import secrets
    return secrets.token_hex(16)


def derive_pw_key(password: str, salt_hex: str) -> bytes:
    """scrypt(password, salt) → urlsafe-b64 Fernet key. Deterministic for a
    given (password, salt) — the user re-derives it from their password."""
    salt = bytes.fromhex(salt_hex)
    raw = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return base64.urlsafe_b64encode(raw)


def make_check(key: bytes) -> str:
    from cryptography.fernet import Fernet
    return Fernet(key).encrypt(_CHECK_SENTINEL).decode("ascii")


def verify_check(key: bytes, check: str | None) -> bool:
    if not check:
        return False
    from cryptography.fernet import Fernet, InvalidToken
    try:
        return Fernet(key).decrypt(check.encode("ascii")) == _CHECK_SENTINEL
    except (InvalidToken, Exception):  # noqa: BLE001
        return False


def is_pw_encrypted(data: bytes) -> bool:
    return bool(data) and data[:len(_PW_MAGIC)] == _PW_MAGIC


def encrypt_blob_pw(data: bytes, key: bytes) -> bytes:
    from cryptography.fernet import Fernet
    return _PW_MAGIC + Fernet(key).encrypt(data)


def decrypt_blob_pw(data: bytes, key: bytes) -> bytes:
    from cryptography.fernet import Fernet
    return Fernet(key).decrypt(data[len(_PW_MAGIC):])
