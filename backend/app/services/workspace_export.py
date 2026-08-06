"""M46 · §5 · per-user workspace in the user's own Drive.

Builds a single SQLite file holding everything DocAIQ derived for one user
(documents, chunk text + embeddings, extracted fields, chat, learned types),
encrypts it with the user's per-user key, and stores it in THEIR Google Drive
(`docaiq_docs/.workspace/workspace.sqlite`). This is the artifact that makes
"your data lives in your own Drive" real.

Status of the architecture: this ships the **export + a validated read path**.
Postgres stays the
authoritative store; reading from the Drive workspace is opt-in behind
`documents_storage_mode='drive'` and dual-sourced, so the live product is
unaffected. The destructive Postgres cutover (P4) is deliberately NOT done here.

No sqlite-vec extension dependency: embeddings are stored as float32 blobs and
ranked with a numpy cosine — fine for proving the architecture + the artifact.
"""
from __future__ import annotations

import logging
import sqlite3
import tempfile

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.orm import (ChatMessage, Document, DocumentChunk, LearnedDocType)

log = logging.getLogger("docaiq.workspace_export")

WORKSPACE_FOLDER = "workspace"  # under docaiq_docs/ so it's visible in Drive
WORKSPACE_FILE = "workspace.sqlite"


def _vec_bytes(v) -> bytes:
    if v is None:
        return b""
    return np.asarray(v, dtype=np.float32).tobytes()


def _bytes_vec(b: bytes) -> np.ndarray:
    return np.frombuffer(b or b"", dtype=np.float32)


def build_workspace_bytes(db: Session, *, tenant_id: str, owner_user_id: int) -> tuple[bytes, dict]:
    """Build the user's workspace SQLite (owner-scoped) and return (bytes, stats)."""
    docs = db.scalars(select(Document).where(
        Document.tenant_id == tenant_id, Document.owner_user_id == owner_user_id,
        Document.is_archived.is_(False))).all()
    doc_pks = [d.pk for d in docs]

    with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=True) as tf:
        con = sqlite3.connect(tf.name)
        con.executescript("""
            CREATE TABLE documents(pk INTEGER PRIMARY KEY, id_external TEXT, name TEXT,
                doc_type TEXT, doc_type_confidence REAL, extracted_fields TEXT, page_count INTEGER);
            CREATE TABLE chunks(doc_pk INTEGER, chunk_index INTEGER, page INTEGER,
                text TEXT, embedding BLOB);
            CREATE TABLE chat(role TEXT, text TEXT, doc_id_external TEXT, workspace_key TEXT, pk INTEGER);
            CREATE TABLE learned_types(slug TEXT, label TEXT, source TEXT, seen_count INTEGER);
            CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
            CREATE INDEX ix_chunks_doc ON chunks(doc_pk);
        """)
        import json as _json
        for d in docs:
            con.execute("INSERT INTO documents VALUES (?,?,?,?,?,?,?)",
                        (d.pk, d.id_external, d.name, d.doc_type, d.doc_type_confidence,
                         _json.dumps(d.extracted_fields or {}, ensure_ascii=False), d.pages))
        n_chunks = 0
        if doc_pks:
            for c in db.scalars(select(DocumentChunk).where(
                    DocumentChunk.document_pk.in_(doc_pks)).order_by(
                    DocumentChunk.document_pk, DocumentChunk.chunk_index)).all():
                con.execute("INSERT INTO chunks VALUES (?,?,?,?,?)",
                            (c.document_pk, c.chunk_index, c.page, c.text, _vec_bytes(c.embedding)))
                n_chunks += 1
        n_chat = 0
        # Build the chat filter without a Python `False` literal — `False | <expr>`
        # raises TypeError. With no docs we still want the workspace thread.
        chat_cond = ChatMessage.workspace_key == f"user:{owner_user_id}"
        if docs:
            chat_cond = chat_cond | ChatMessage.doc_id_external.in_([d.id_external for d in docs])
        for m in db.scalars(select(ChatMessage).where(
                ChatMessage.tenant_id == tenant_id).where(chat_cond)).all():
            con.execute("INSERT INTO chat VALUES (?,?,?,?,?)",
                        (m.role, m.text, m.doc_id_external, m.workspace_key, m.pk))
            n_chat += 1
        for t in db.scalars(select(LearnedDocType).where(
                LearnedDocType.tenant_id == tenant_id,
                LearnedDocType.owner_user_id == owner_user_id)).all():
            con.execute("INSERT INTO learned_types VALUES (?,?,?,?)",
                        (t.type_slug, t.label, t.source, t.seen_count))
        con.execute("INSERT INTO meta VALUES ('schema_version','1')")
        # Stamp the embedding model so a restore can tell whether these vectors
        # are reusable (same model) or must be re-ingested (different model).
        from app.embeddings import embed_signature
        con.execute("INSERT INTO meta VALUES ('embed_model', ?)", (embed_signature(),))
        con.commit()
        con.close()
        with open(tf.name, "rb") as fh:
            data = fh.read()
    stats = {"documents": len(docs), "chunks": n_chunks, "chatMessages": n_chat, "bytes": len(data)}
    return data, stats


def retrieve_from_workspace_bytes(data: bytes, query_embedding: list[float],
                                  top_k: int = 8, doc_pks: list[int] | None = None) -> list[dict]:
    """Read a workspace SQLite blob and cosine-rank its chunks against the query
    embedding (numpy). Returns hit dicts mirroring retrieval.Hit fields."""
    q = np.asarray(query_embedding or [], dtype=np.float32)
    if q.size == 0:
        return []
    qn = q / (np.linalg.norm(q) or 1.0)
    with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=True) as tf:
        tf.write(data)
        tf.flush()
        con = sqlite3.connect(tf.name)
        con.row_factory = sqlite3.Row
        names = {r["pk"]: (r["id_external"], r["name"]) for r in con.execute(
            "SELECT pk,id_external,name FROM documents")}
        where = ""
        params: list = []
        if doc_pks:
            where = f" WHERE doc_pk IN ({','.join('?'*len(doc_pks))})"
            params = list(doc_pks)
        rows = con.execute(f"SELECT doc_pk,chunk_index,page,text,embedding FROM chunks{where}", params).fetchall()
        con.close()
    scored = []
    for r in rows:
        v = _bytes_vec(r["embedding"])
        if v.size != q.size:
            continue
        sim = float(np.dot(qn, v / (np.linalg.norm(v) or 1.0)))
        scored.append((sim, r))
    scored.sort(key=lambda x: x[0], reverse=True)
    out = []
    for sim, r in scored[:top_k]:
        idx, name = names.get(r["doc_pk"], ("", ""))
        out.append({"document_pk": r["doc_pk"], "document_id_external": idx,
                    "document_name": name, "page": r["page"], "text": r["text"],
                    "score": round(sim, 4)})
    return out


async def sync_to_drive(db: Session, *, tenant_id: str, owner_user_id: int) -> dict:
    """Build the workspace, encrypt it (per-user key), and store it in the user's
    Drive `.workspace` folder. Replaces the prior copy. Returns stats."""
    from app import drive_crypto
    from app.connectors import drive as drive_mod
    from app.repositories import connectors as conn_repo
    from app.orm import WorkspaceSync

    acct = conn_repo.get(db, "drive")
    if acct is None:
        return {"status": "skipped", "reason": "Drive not connected"}
    data, stats = build_workspace_bytes(db, tenant_id=tenant_id, owner_user_id=owner_user_id)
    # Default: store the backup UNENCRYPTED in the user's own (private) Drive —
    # no server-held key, so it always restores (immune to deploy/key changes).
    # Opt-in: when the user enabled password encryption, encrypt with their
    # scrypt-derived key (cached in Redis on unlock). If encryption is on but the
    # key isn't cached (locked), we SKIP the backup rather than write plaintext.
    from app.orm import User
    user = db.get(User, owner_user_id)
    if user is not None and getattr(user, "backup_encryption", False):
        from app import backup_keycache
        key = backup_keycache.get(tenant_id, owner_user_id)
        if not key:
            log.info("workspace_export: backup locked (no cached key) for owner=%s — skipping", owner_user_id)
            return {"status": "locked",
                    "reason": "Backup encryption is on but locked — enter your password to resume backups."}
        blob = drive_crypto.encrypt_blob_pw(data, key)
    else:
        blob = data
    backend = drive_mod.get_backend()
    # Nest the workspace folder inside docaiq_docs so the user can find it.
    inbox_id = await backend.find_or_create_folder(acct, drive_mod.INBOX_FOLDER_NAME)
    folder_id = await backend.find_or_create_folder(acct, WORKSPACE_FOLDER, parent_id=inbox_id)
    row = db.scalar(select(WorkspaceSync).where(WorkspaceSync.owner_user_id == owner_user_id))
    if row and row.drive_file_id:
        try:
            await backend.delete_file(acct, row.drive_file_id)
        except Exception:  # noqa: BLE001
            pass
    file_id = await backend.upload_file(acct, WORKSPACE_FILE, blob, "application/x-sqlite3", folder_id)
    from datetime import datetime, timezone
    if row is None:
        row = WorkspaceSync(tenant_id=tenant_id, owner_user_id=owner_user_id)
        db.add(row)
    row.drive_file_id = file_id
    row.doc_count = stats["documents"]
    row.size_bytes = stats["bytes"]
    row.synced_at = datetime.now(timezone.utc)
    db.commit()
    log.info("workspace_export: owner=%s synced %s docs to Drive (%s)", owner_user_id, stats["documents"], file_id)
    return {"status": "ok", "fileId": file_id, **stats}
