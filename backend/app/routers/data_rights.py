"""M46 · §compliance · GDPR/PDPA data-subject rights endpoints (documents).

- GET    /api/me/export  → download everything we hold for you (DSAR, Arts 15/20)
- DELETE /api/me         → erase it all + your account (Art 17)

Documents product only; both run in the caller's own owner scope.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_current_tenant, get_session
from app.documents_scope import get_current_owner_user_pk
from app.orm import User
from app.routers.auth import _clear_session_cookie
from app.security import CurrentUser, get_current_user

log = logging.getLogger("docaiq.data_rights")
router = APIRouter()


def _guard() -> None:
    if get_settings().product != "documents":
        raise HTTPException(status_code=404, detail="Not available")


@router.get("/me/consent")
def get_consent(db: Session = Depends(get_session),
                user: CurrentUser = Depends(get_current_user)) -> dict:
    """Which consents the caller has accepted at the current version, plus whether
    model-training consent is currently REQUIRED (free plan + not yet given)."""
    _guard()
    from app.services import consent as consent_svc
    from app.services import subscriptions as subs
    st = consent_svc.status(db, tenant_id=get_current_tenant(),
                            user_id=get_current_owner_user_pk())
    uid = get_current_owner_user_pk()
    is_free = False
    try:
        u = db.get(User, uid) if uid is not None else None
        is_free = bool(u is not None and subs.effective_plan(u) == "free")
    except Exception:  # noqa: BLE001
        is_free = False
    st["modelTrainingRequired"] = bool(is_free and not st.get("modelTraining"))
    return st


class ConsentPayload(BaseModel):
    kind: str


@router.post("/me/consent")
def post_consent(payload: ConsentPayload, db: Session = Depends(get_session),
                 user: CurrentUser = Depends(get_current_user)) -> dict:
    """Record the caller's acceptance of a consent kind (e.g. 'personal_data')."""
    _guard()
    from app.services import consent as consent_svc
    if payload.kind not in (consent_svc.KIND_PROCESSING, consent_svc.KIND_PERSONAL_DATA,
                            consent_svc.KIND_MODEL_TRAINING):
        raise HTTPException(status_code=422, detail="Unknown consent kind")
    consent_svc.record(db, tenant_id=get_current_tenant(),
                       user_id=get_current_owner_user_pk(), kind=payload.kind)
    db.commit()
    return consent_svc.status(db, tenant_id=get_current_tenant(),
                              user_id=get_current_owner_user_pk())


class RedeemPromoPayload(BaseModel):
    code: str


@router.post("/me/redeem-promo")
def redeem_promo(payload: RedeemPromoPayload, db: Session = Depends(get_session),
                 user: CurrentUser = Depends(get_current_user)) -> dict:
    """Redeem a promo code → grant its paid plan for the code's duration."""
    _guard()
    from app.services import subscriptions as subs
    u = db.get(User, user.id)
    if u is None:
        raise HTTPException(status_code=404, detail="User not found")
    return {"ok": True, **subs.redeem_promo(db, user=u, code=payload.code)}


@router.get("/me/export")
def export_my_data(db: Session = Depends(get_session),
                   user: CurrentUser = Depends(get_current_user)) -> dict:
    """Everything DocAIQ holds for the caller, as JSON (data access + portability)."""
    _guard()
    uid = get_current_owner_user_pk()
    if uid is None:
        raise HTTPException(status_code=400, detail="No user in context")
    from app.services import data_rights
    return data_rights.export_user_data(db, uid=uid, email=user.email,
                                        tenant_id=get_current_tenant())


@router.delete("/me")
def erase_my_account(response: Response, db: Session = Depends(get_session),
                     user: CurrentUser = Depends(get_current_user)) -> dict:
    """Permanently erase the caller's documents, chat, learning artifacts, groups
    they own, memberships, connector tokens, AND the account itself. Irreversible.
    Clears the session cookie so the now-deleted account is logged out."""
    _guard()
    uid = get_current_owner_user_pk()
    if uid is None:
        raise HTTPException(status_code=400, detail="No user in context")
    from app.services import data_rights
    counts = data_rights.erase_user_data(db, uid=uid, email=user.email,
                                         tenant_id=get_current_tenant())
    _clear_session_cookie(response)
    return {"erased": True, "counts": counts}
