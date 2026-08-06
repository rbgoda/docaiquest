"""M46 · Documents System · sharing groups.

A user creates a group, adds members by gmail, and shares individual documents
into it. The group is backed by a shared Google Drive folder (created in the
creator's Drive, shared to each member) so shared docs stay user-owned-in-Drive.
Every member can view + manage the group's shared docs (the per-user owner scope
already grants members access to docs whose group_id is one of theirs).

Documents-product only (404 elsewhere); owner-scoped.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.config import get_settings
from app.connectors import drive as drive_mod
from app.db import get_current_tenant, get_session
from app.documents_scope import get_current_owner_user_pk
from app.orm import (Document, DocumentGroup, DocumentGroupEvent,
                     DocumentGroupMember, DocumentGroupShare)
from app.repositories import connectors as conn_repo
from app.repositories import documents as doc_repo
from app.repositories import users as users_repo
from app.security import CurrentUser, get_current_user

log = logging.getLogger("docaiq.groups")
router = APIRouter()


def link_pending_group_invites(db: Session, user_pk: int, email: str) -> int:
    """Attach any PENDING group memberships (user_id IS NULL) for this email to
    a newly-created account. Called from the documents signup path. Safe no-op in
    the auditing product (no document_group_members rows). Returns rows linked."""
    if not email:
        return 0
    n = db.query(DocumentGroupMember).filter(
        DocumentGroupMember.member_email == email.strip().lower(),
        DocumentGroupMember.user_id.is_(None),
    ).update({DocumentGroupMember.user_id: user_pk}, synchronize_session=False)
    return int(n or 0)


def _record_event(db: Session, group_id: int, user: "CurrentUser", action: str,
                  detail: str | None = None) -> None:
    """Append a group activity-log row. Never raises (best-effort)."""
    try:
        db.add(DocumentGroupEvent(
            tenant_id=get_current_tenant(), group_id=group_id,
            actor_user_id=get_current_owner_user_pk(),
            actor_email=getattr(user, "email", None),
            action=action, detail=(detail or "")[:512] or None))
    except Exception as e:  # noqa: BLE001
        log.warning("group %s: event log (%s) failed: %s", group_id, action, e)


def _guard() -> None:
    if get_settings().product != "documents":
        raise HTTPException(status_code=404, detail="Not available")


def _my_group_or_404(db: Session, group_id: int) -> DocumentGroup:
    """Load a group the caller is a member of, or 404."""
    tid = get_current_tenant()
    uid = get_current_owner_user_pk()
    g = db.scalar(select(DocumentGroup).where(
        DocumentGroup.pk == group_id, DocumentGroup.tenant_id == tid))
    if g is None:
        raise HTTPException(status_code=404, detail="Group not found")
    member = db.scalar(select(DocumentGroupMember).where(
        DocumentGroupMember.group_id == group_id,
        DocumentGroupMember.user_id == uid))
    if member is None:
        raise HTTPException(status_code=403, detail="Not a member of this group")
    return g


def _group_dict(db: Session, g: DocumentGroup, uid: int) -> dict:
    members = db.scalars(select(DocumentGroupMember).where(
        DocumentGroupMember.group_id == g.pk).order_by(DocumentGroupMember.pk)).all()
    doc_count = db.scalar(select(func.count()).select_from(DocumentGroupShare).where(
        DocumentGroupShare.group_id == g.pk)) or 0
    return {
        "id": g.pk, "name": g.name,
        "createdBy": g.created_by_email,
        "isOwner": g.created_by_user_id == uid,
        "driveShared": g.drive_folder_id is not None,
        "docCount": int(doc_count),
        "members": [{"email": m.member_email, "role": m.role,
                     "pending": m.user_id is None} for m in members],
    }


class CreateGroup(BaseModel):
    name: str


@router.post("/groups")
async def create_group(payload: CreateGroup, db: Session = Depends(get_session),
                       user: CurrentUser = Depends(get_current_user)) -> dict:
    _guard()
    name = (payload.name or "").strip()
    if not name:
        raise HTTPException(status_code=422, detail="name is required")
    tid = get_current_tenant()
    uid = get_current_owner_user_pk()
    # M47 · groups are a Pro feature.
    from app.services import subscriptions as subs
    subs.enforce_feature(db, owner_user_id=uid, feature="groups")
    g = DocumentGroup(tenant_id=tid, name=name, created_by_user_id=uid,
                      created_by_email=user.email)
    db.add(g)
    db.flush()  # get pk
    # Back it with a shared Drive folder when Drive is connected.
    acct = conn_repo.get(db, "drive")
    if acct is not None:
        try:
            g.drive_folder_id = await drive_mod.get_backend().find_or_create_folder(
                acct, f"docaiq_group_{g.pk}")
        except Exception as e:  # noqa: BLE001 — group still usable without Drive
            log.warning("group %s: drive folder create failed: %s", g.pk, e)
    # Creator is the owner member.
    db.add(DocumentGroupMember(tenant_id=tid, group_id=g.pk, user_id=uid,
                               member_email=user.email, role="owner"))
    _record_event(db, g.pk, user, "created", g.name)
    db.commit()
    return _group_dict(db, g, uid)


@router.get("/groups")
def list_groups(db: Session = Depends(get_session),
                user: CurrentUser = Depends(get_current_user)) -> dict:
    _guard()
    tid = get_current_tenant()
    uid = get_current_owner_user_pk()
    my_group_ids = select(DocumentGroupMember.group_id).where(
        DocumentGroupMember.user_id == uid)
    groups = db.scalars(select(DocumentGroup).where(
        DocumentGroup.tenant_id == tid, DocumentGroup.pk.in_(my_group_ids)
    ).order_by(DocumentGroup.pk.desc())).all()
    return {"groups": [_group_dict(db, g, uid) for g in groups]}


class AddMember(BaseModel):
    email: EmailStr


@router.post("/groups/{group_id}/members")
async def add_member(group_id: int, payload: AddMember,
                     db: Session = Depends(get_session),
                     user: CurrentUser = Depends(get_current_user)) -> dict:
    _guard()
    g = _my_group_or_404(db, group_id)
    tid = get_current_tenant()
    uid = get_current_owner_user_pk()
    email = str(payload.email).strip().lower()
    # Existing DocAIQ user (so they can see group docs in the app). When no
    # account uses that email yet, add a PENDING member (user_id=None) — the
    # Drive share still works by email, and the membership auto-links to their
    # account the moment they register (see link_pending_group_invites).
    member_user = users_repo.get_by_email(db, email)
    exists = db.scalar(select(DocumentGroupMember).where(
        DocumentGroupMember.group_id == group_id,
        DocumentGroupMember.member_email == email))
    if exists is None:
        db.add(DocumentGroupMember(
            tenant_id=tid, group_id=group_id,
            user_id=(member_user.pk if member_user else None),
            member_email=email, role="member"))
        _record_event(db, group_id, user,
                      "invited_member" if member_user is None else "added_member", email)
    elif member_user is not None and exists.user_id is None:
        # Was pending, account now exists → link it.
        exists.user_id = member_user.pk
    # Share the group's Drive folder with the member's Google account.
    acct = conn_repo.get(db, "drive")
    if acct is not None and g.drive_folder_id:
        try:
            await drive_mod.get_backend().share_folder(acct, g.drive_folder_id, email, "writer")
        except Exception as e:  # noqa: BLE001
            log.warning("group %s: drive share to %s failed: %s", group_id, email, e)
    db.commit()
    return _group_dict(db, g, uid)


@router.delete("/groups/{group_id}/members/{email}")
async def remove_member(group_id: int, email: str, db: Session = Depends(get_session),
                        user: CurrentUser = Depends(get_current_user)) -> dict:
    _guard()
    g = _my_group_or_404(db, group_id)
    uid = get_current_owner_user_pk()
    email = email.strip().lower()
    if email == (g.created_by_email or "").lower():
        raise HTTPException(status_code=400, detail="Can't remove the group owner")
    db.query(DocumentGroupMember).filter(
        DocumentGroupMember.group_id == group_id,
        DocumentGroupMember.member_email == email).delete()
    _record_event(db, group_id, user, "removed_member", email)
    # §1 · revoke the member's access to the group's shared Drive folder, not
    # just the DB row (best-effort — they keep no lingering Drive access).
    acct = conn_repo.get(db, "drive")
    if acct is not None and g.drive_folder_id:
        try:
            await drive_mod.get_backend().revoke_folder(acct, g.drive_folder_id, email)
        except Exception as e:  # noqa: BLE001
            log.warning("group %s: drive revoke for %s failed: %s", group_id, email, e)
    db.commit()
    return _group_dict(db, g, uid)


class RenameGroup(BaseModel):
    name: str


@router.patch("/groups/{group_id}")
def rename_group(group_id: int, payload: RenameGroup,
                 db: Session = Depends(get_session),
                 user: CurrentUser = Depends(get_current_user)) -> dict:
    """Rename a group. Owner-only."""
    _guard()
    g = _my_group_or_404(db, group_id)
    if g.created_by_user_id != get_current_owner_user_pk():
        raise HTTPException(status_code=403, detail="Only the group owner can rename the group")
    name = (payload.name or "").strip()
    if not name:
        raise HTTPException(status_code=422, detail="name is required")
    old = g.name
    g.name = name
    _record_event(db, g.pk, user, "renamed", f"{old} → {name}")
    db.commit()
    return _group_dict(db, g, get_current_owner_user_pk())


@router.delete("/groups/{group_id}")
def delete_group(group_id: int, db: Session = Depends(get_session),
                 user: CurrentUser = Depends(get_current_user)) -> dict:
    """Delete a group. Its documents revert to PERSONAL (group_id cleared — each
    stays owned by whoever shared it). Only the group owner may delete."""
    _guard()
    g = _my_group_or_404(db, group_id)
    if g.created_by_user_id != get_current_owner_user_pk():
        raise HTTPException(status_code=403, detail="Only the group owner can delete the group")
    # Docs that become personal: shared into THIS group but no OTHER group.
    other = select(DocumentGroupShare.document_pk).where(
        DocumentGroupShare.group_id != group_id).subquery()
    moved = db.scalar(select(func.count()).select_from(DocumentGroupShare).where(
        DocumentGroupShare.group_id == group_id,
        DocumentGroupShare.document_pk.notin_(select(other.c.document_pk)))) or 0
    db.query(DocumentGroupShare).filter(DocumentGroupShare.group_id == group_id).delete()
    db.query(DocumentGroupMember).filter(DocumentGroupMember.group_id == group_id).delete()
    db.delete(g)
    db.commit()
    return {"deleted": True, "docsMovedToPersonal": int(moved)}


@router.get("/groups/{group_id}/documents")
def group_documents(group_id: int, db: Session = Depends(get_session),
                    user: CurrentUser = Depends(get_current_user)) -> dict:
    _guard()
    _my_group_or_404(db, group_id)
    in_group = select(DocumentGroupShare.document_pk).where(
        DocumentGroupShare.group_id == group_id)
    rows = db.scalars(select(Document).where(Document.pk.in_(in_group))
                      .order_by(Document.pk.desc())).all()
    gids = doc_repo._group_ids_for(db, [r.pk for r in rows])  # noqa: SLF001
    return {"documents": [doc_repo._to_dict(r, group_ids=gids.get(r.pk, []))  # noqa: SLF001
                          for r in rows]}


@router.get("/groups/{group_id}/chat")
def group_chat_thread(group_id: int, db: Session = Depends(get_session),
                      user: CurrentUser = Depends(get_current_user)) -> dict:
    """A1 · the per-group 'ask across this group's documents' thread. Members only."""
    _guard()
    _my_group_or_404(db, group_id)
    from app.services import workspace_chat as wc
    return wc.get_thread(db, get_current_tenant(), None, group_id=group_id)


class GroupChatMessage(BaseModel):
    text: str


@router.post("/groups/{group_id}/chat/messages")
def group_chat_post(group_id: int, payload: GroupChatMessage,
                    db: Session = Depends(get_session),
                    user: CurrentUser = Depends(get_current_user)) -> dict:
    """A1 · ask a question across the group's shared documents. Members only."""
    _guard()
    _my_group_or_404(db, group_id)
    text = (payload.text or "").strip()
    if not text:
        raise HTTPException(status_code=422, detail="Empty message")
    from app.services import workspace_chat as wc
    return wc.post_message(db, get_current_tenant(), None, text, group_id=group_id)


@router.get("/groups/{group_id}/activity")
def group_activity(group_id: int, db: Session = Depends(get_session),
                   user: CurrentUser = Depends(get_current_user)) -> dict:
    """Recent activity for a group (most recent first). Any member can read it."""
    _guard()
    _my_group_or_404(db, group_id)
    rows = db.scalars(select(DocumentGroupEvent)
                      .where(DocumentGroupEvent.group_id == group_id)
                      .order_by(DocumentGroupEvent.pk.desc()).limit(50)).all()
    return {"events": [{
        "id": e.pk, "actor": e.actor_email, "action": e.action,
        "detail": e.detail, "at": e.created_at.isoformat() if e.created_at else None,
    } for e in rows]}


class ShareToGroup(BaseModel):
    # The exact set of groups this doc should belong to (checkbox selection).
    # Accepts a single groupId too, for back-compat.
    groupIds: list[int] | None = None
    groupId: int | None = None


@router.post("/documents/{doc_id}/share-to-group")
async def share_to_group(doc_id: str, payload: ShareToGroup,
                         db: Session = Depends(get_session),
                         user: CurrentUser = Depends(get_current_user)) -> dict:
    """Set which of the caller's groups a document is shared into. The request
    carries the FULL desired set (checkbox selection): groups added get a Drive
    copy + share row; groups unchecked get their share removed. Members of any
    selected group can then see + manage the doc."""
    _guard()
    doc = doc_repo.get_row(db, doc_id)  # visible to caller (owner or group member)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    uid = get_current_owner_user_pk()
    # Only the document's owner may change its group allocation. Other members
    # can view + chat with it, but allocation/provenance stays with the owner.
    if doc.owner_user_id != uid:
        raise HTTPException(status_code=403,
                            detail="Only the document's owner can change its group sharing.")
    # Desired set, validated to groups the caller actually belongs to.
    desired_raw = list(payload.groupIds or ([] if payload.groupId is None else [payload.groupId]))
    desired: list[int] = []
    for gid in desired_raw:
        if gid not in desired:
            _my_group_or_404(db, gid)  # 403/404 if not a member
            desired.append(gid)
    # Current shares for this doc, limited to MY groups (don't touch others').
    my_groups = {m.group_id for m in db.scalars(select(DocumentGroupMember).where(
        DocumentGroupMember.user_id == uid)).all()}
    current = {s.group_id: s for s in db.scalars(select(DocumentGroupShare).where(
        DocumentGroupShare.document_pk == doc.pk)).all() if s.group_id in my_groups}
    tid = get_current_tenant()
    to_add = [gid for gid in desired if gid not in current]
    to_remove = [gid for gid in current if gid not in desired]
    acct = conn_repo.get(db, "drive")
    for gid in to_remove:
        share = current[gid]
        # A2 · delete the group's Drive copy of this doc (best-effort), not just
        # the DB row, so unshare leaves nothing behind.
        if acct is not None and share.drive_copy_file_id:
            try:
                await drive_mod.get_backend().delete_file(acct, share.drive_copy_file_id)
            except Exception as e:  # noqa: BLE001
                log.warning("share_to_group: drive copy delete failed for doc %s ← group %s: %s",
                            doc_id, gid, e)
        db.delete(share)
        _record_event(db, gid, user, "unshared_doc", doc.name)
    for gid in to_add:
        new_share = DocumentGroupShare(tenant_id=tid, document_pk=doc.pk, group_id=gid)
        db.add(new_share)
        _record_event(db, gid, user, "shared_doc", doc.name)
        g = db.get(DocumentGroup, gid)
        # Copy the file into the group folder (best-effort) so it's Drive-shared.
        if acct is not None and g is not None and g.drive_folder_id:
            try:
                from app import storage
                blob = None
                if doc.s3_key:
                    blob = storage.get_object_bytes(doc.s3_key)
                elif doc.source == "drive" and doc.source_ref:
                    pulled = await drive_mod.get_backend().fetch(acct, doc.source_ref)
                    from app import drive_crypto  # B7 · decrypt owner's copy before re-sharing
                    blob = drive_crypto.decrypt_blob(doc.owner_user_id, pulled.body)
                if blob is not None:
                    new_share.drive_copy_file_id = await drive_mod.get_backend().upload_file(
                        acct, doc.name, blob, doc.mime_type or "application/octet-stream",
                        g.drive_folder_id)
            except Exception as e:  # noqa: BLE001 — association still proceeds
                log.warning("share_to_group: drive copy failed for doc %s → group %s: %s",
                            doc_id, gid, e)
    db.commit()
    return {"shared": True, "docId": doc_id, "groupIds": desired}
