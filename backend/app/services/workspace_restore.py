"""M47 · §5 · restore a user's workspace from their own Google Drive snapshot.

The mirror image of workspace_export: locate `docaiq_docs/workspace/workspace.sqlite`
in the user's Drive, decrypt it (recovering a prior account key if the pk changed
— see drive_crypto.decrypt_blob_recover), and re-apply the derived layer that a
plain inbox re-sync does NOT regenerate:

  · doc_type / confidence / extracted_fields  (classifications)
  · learned_doc_types vocabulary               (self-learning memory)
  · chunks + embeddings                        (only for docs that have none, e.g.
                                                an original missing from Drive)
  · chat history                               (remapped to the new doc ids)

This is the disaster-recovery path: if an account is wiped/recreated, the user
logs in, we detect the snapshot, and offer a one-click restore. Postgres stays
authoritative; this only writes the caller's own rows. Matching is by filename
(the snapshot predates re-sync, so id_external differs but names are stable).
"""
from __future__ import annotations

import json
import logging
import sqlite3
import tempfile

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.connectors import drive as drive_mod
from app.orm import ChatMessage, Document, DocumentChunk, LearnedDocType
from app.services.workspace_export import WORKSPACE_FILE, WORKSPACE_FOLDER

log = logging.getLogger("docaiq.workspace_restore")


async def locate_snapshot(acct, backend):
    """Return the DriveFile for docaiq_docs/workspace/workspace.sqlite, or None."""
    inbox_id = await backend.find_or_create_folder(acct, drive_mod.INBOX_FOLDER_NAME)
    folder_id = await backend.find_or_create_folder(acct, WORKSPACE_FOLDER, parent_id=inbox_id)
    for f in await backend.list_files(acct, folder_id):
        if f.name == WORKSPACE_FILE:
            return f
    return None


def _open(data: bytes):
    tf = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
    tf.write(data)
    tf.flush()
    tf.close()
    con = sqlite3.connect(tf.name)
    con.row_factory = sqlite3.Row
    return con


def read_stats(data: bytes) -> dict:
    """Cheap counts from a decrypted snapshot for the restore prompt."""
    con = _open(data)
    try:
        def c(sql):
            try:
                return int(con.execute(sql).fetchone()[0])
            except Exception:
                return 0
        return {
            "documents": c("select count(*) from documents"),
            "chunks": c("select count(*) from chunks"),
            "chatMessages": c("select count(*) from chat"),
            "learnedTypes": c("select count(*) from learned_types"),
        }
    finally:
        con.close()


def apply_snapshot(db: Session, *, tenant_id: str, owner_user_id: int, data: bytes) -> dict:
    """Re-apply the snapshot's derived layer onto the caller's current docs.
    Returns a summary of what was restored. Idempotent-ish: classifications are
    overwritten from the snapshot; learned types upsert by slug; chunks/chat are
    only filled where currently empty so re-running doesn't duplicate."""
    settings = get_settings()
    dim = getattr(settings, "embed_dim", 384)
    con = _open(data)
    summary = {"docsMatched": 0, "typesRestored": 0, "fieldsRestored": 0,
               "learnedTypes": 0, "chunksRestored": 0, "chatRestored": 0, "unmatched": []}
    try:
        # current docs by name (owner-scoped)
        cur = db.scalars(select(Document).where(
            Document.tenant_id == tenant_id, Document.owner_user_id == owner_user_id)).all()
        by_name: dict[str, list[Document]] = {}
        for d in cur:
            by_name.setdefault(d.name, []).append(d)
        old_to_name: dict[str, str] = {}

        # 1) classifications + extracted fields, matched by filename
        for r in con.execute("select id_external, name, doc_type, doc_type_confidence, extracted_fields from documents"):
            old_to_name[r["id_external"]] = r["name"]
            targets = by_name.get(r["name"], [])
            if not targets:
                summary["unmatched"].append(r["name"])
                continue
            summary["docsMatched"] += 1
            for d in targets:
                if r["doc_type"]:
                    d.doc_type = r["doc_type"]
                    d.doc_type_confidence = r["doc_type_confidence"]
                    summary["typesRestored"] += 1
                if r["extracted_fields"]:
                    try:
                        ef = json.loads(r["extracted_fields"])
                        if ef and not d.extracted_fields:
                            d.extracted_fields = ef
                            summary["fieldsRestored"] += 1
                    except Exception:  # noqa: BLE001
                        pass

        # 2) chunks + embeddings — only for docs that currently have none
        #    (e.g. ingest failed, or the original is no longer in Drive), AND
        #    only when the snapshot's embedding model matches the live one.
        #    Vectors from a different model share the dimension but NOT the
        #    vector space, so reusing them would silently break semantic search.
        #    On mismatch (or an old, unstamped snapshot) we SKIP chunks here so
        #    the doc is re-ingested + re-embedded with the current model instead.
        from app.embeddings import embed_signature
        snap_model = None
        try:
            mrow = con.execute("select value from meta where key='embed_model'").fetchone()
            snap_model = mrow["value"] if mrow else None
        except Exception:  # noqa: BLE001 — old snapshot without a meta row
            snap_model = None
        chunks_compatible = bool(snap_model) and snap_model == embed_signature()
        if not chunks_compatible:
            summary["chunksSkippedModelMismatch"] = True
            summary["snapshotEmbedModel"] = snap_model or "unknown"
        rows = (con.execute("select doc_pk, chunk_index, page, text, embedding from chunks").fetchall()
                if chunks_compatible else [])
        chunks_by_oldpk: dict[int, list] = {}
        for r in rows:
            chunks_by_oldpk.setdefault(r["doc_pk"], []).append(r)
        # map snapshot doc pk → name → current docs
        oldpk_name = {r["pk"]: r["name"] for r in con.execute("select pk, name from documents")}
        for oldpk, crows in chunks_by_oldpk.items():
            name = oldpk_name.get(oldpk)
            for d in by_name.get(name, []):
                has = db.scalar(select(DocumentChunk.pk).where(DocumentChunk.document_pk == d.pk).limit(1))
                if has:
                    continue
                for cr in crows:
                    emb = np.frombuffer(cr["embedding"] or b"", dtype=np.float32)
                    if emb.size != dim:
                        continue
                    txt = cr["text"] or ""
                    db.add(DocumentChunk(
                        tenant_id=tenant_id, document_pk=d.pk, chunk_index=cr["chunk_index"],
                        text=txt, kind="text", page=cr["page"] or 1,
                        char_start=0, char_end=len(txt), embedding=emb.tolist()))
                    summary["chunksRestored"] += 1

        # 3) learned-type vocabulary — upsert by slug
        for r in con.execute("select slug, label, source, seen_count from learned_types"):
            if not r["slug"]:
                continue
            existing = db.scalar(select(LearnedDocType).where(
                LearnedDocType.tenant_id == tenant_id,
                LearnedDocType.owner_user_id == owner_user_id,
                LearnedDocType.type_slug == r["slug"]))
            if existing is None:
                db.add(LearnedDocType(
                    tenant_id=tenant_id, owner_user_id=owner_user_id, type_slug=r["slug"],
                    label=r["label"], source=r["source"] or "ai", seen_count=r["seen_count"] or 0))
                summary["learnedTypes"] += 1

        # 4) chat — remap old doc id_external → new, insert when the target is empty
        new_id_for_name = {name: docs[0].id_external for name, docs in by_name.items() if docs}
        for r in con.execute("select role, text, doc_id_external, workspace_key from chat"):
            new_doc_id = None
            ws_key = None
            if r["doc_id_external"]:
                nm = old_to_name.get(r["doc_id_external"])
                new_doc_id = new_id_for_name.get(nm)
                if new_doc_id is None:
                    continue  # the doc this chat belonged to isn't present
                exists = db.scalar(select(ChatMessage.pk).where(
                    ChatMessage.tenant_id == tenant_id,
                    ChatMessage.doc_id_external == new_doc_id).limit(1))
                if exists:
                    continue  # already has chat — don't duplicate
            elif r["workspace_key"]:
                ws_key = f"user:{owner_user_id}"
                exists = db.scalar(select(ChatMessage.pk).where(
                    ChatMessage.tenant_id == tenant_id,
                    ChatMessage.workspace_key == ws_key).limit(1))
                if exists:
                    continue
            else:
                continue
            db.add(ChatMessage(tenant_id=tenant_id, role=r["role"] or "user", text=r["text"] or "",
                               doc_id_external=new_doc_id, workspace_key=ws_key))
            summary["chatRestored"] += 1

        db.commit()
    finally:
        con.close()
    summary["unmatched"] = summary["unmatched"][:20]
    log.info("workspace_restore: owner=%s restored %s", owner_user_id, summary)
    return summary
