"""M50 · encrypt existing connector OAuth tokens at rest.

Re-encrypts plaintext access_token/refresh_token in connector_accounts using the
per-owner drive_crypto key. SELF-CHECKING: a token is only rewritten if
decrypt(encrypt(x)) == x for that row, so a key/env problem can never leave a
row in an unreadable state (it stays plaintext, which the read path still
handles via the backward-compatible passthrough).
"""
from typing import Sequence, Union

from alembic import op
from sqlalchemy import text

revision: str = "0068_encrypt_connector_tokens"
down_revision: Union[str, Sequence[str], None] = "0067_chat_doc_index"
branch_labels = None
depends_on = None

_PREFIX = "DQTOK1:"


def upgrade() -> None:
    try:
        from app import drive_crypto
    except Exception:  # noqa: BLE001 — if app isn't importable, skip (read path handles plaintext)
        return
    conn = op.get_bind()
    rows = conn.execute(text(
        "SELECT pk, owner_user_id, access_token, refresh_token FROM connector_accounts"
    )).fetchall()
    for pk, owner, at, rt in rows:
        new_at, new_rt = at, rt
        if at and not at.startswith(_PREFIX):
            enc = drive_crypto.encrypt_token(owner, at)
            if drive_crypto.decrypt_token(owner, enc) == at:  # round-trip guard
                new_at = enc
        if rt and not rt.startswith(_PREFIX):
            enc = drive_crypto.encrypt_token(owner, rt)
            if drive_crypto.decrypt_token(owner, enc) == rt:
                new_rt = enc
        if new_at != at or new_rt != rt:
            conn.execute(text(
                "UPDATE connector_accounts SET access_token=:at, refresh_token=:rt WHERE pk=:pk"
            ), {"at": new_at, "rt": new_rt, "pk": pk})


def downgrade() -> None:
    try:
        from app import drive_crypto
    except Exception:  # noqa: BLE001
        return
    conn = op.get_bind()
    rows = conn.execute(text(
        "SELECT pk, owner_user_id, access_token, refresh_token FROM connector_accounts"
    )).fetchall()
    for pk, owner, at, rt in rows:
        new_at = drive_crypto.decrypt_token(owner, at) if at else at
        new_rt = drive_crypto.decrypt_token(owner, rt) if rt else rt
        if new_at != at or new_rt != rt:
            conn.execute(text(
                "UPDATE connector_accounts SET access_token=:at, refresh_token=:rt WHERE pk=:pk"
            ), {"at": new_at, "rt": new_rt, "pk": pk})
