"""Fleet sync — public endpoints app instances call to register + heartbeat.

The CENTRAL (shared) DocAIQ instance hosts these; Enterprise dedicated containers
POST here on boot + periodically. Gated by a shared `fleet_token` (X-Fleet-Token),
NOT a user session. Admin approve/revoke + listing live in routers/superadmin.py.
"""
from __future__ import annotations

import datetime as _dt

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_session
from app.orm import AppInstance

router = APIRouter()


def _now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def require_fleet_token(x_fleet_token: str | None = Header(None)) -> None:
    token = get_settings().fleet_token
    if not token:
        raise HTTPException(status_code=404, detail="fleet not enabled")
    if x_fleet_token != token:
        raise HTTPException(status_code=401, detail="bad fleet token")


class RegisterPayload(BaseModel):
    instanceId: str
    name: str | None = None
    hostname: str | None = None
    version: str | None = None
    plan: str = "enterprise"


@router.post("/sync/register")
def register(payload: RegisterPayload, request: Request,
             db: Session = Depends(get_session),
             _t: None = Depends(require_fleet_token)) -> dict:
    if not payload.instanceId:
        raise HTTPException(status_code=400, detail="instanceId required")
    row = db.get(AppInstance, payload.instanceId)
    ip = request.client.host if request.client else None
    if row is None:
        row = AppInstance(instance_id=payload.instanceId, status="pending")
        db.add(row)
    row.name = payload.name or row.name
    row.hostname = payload.hostname or row.hostname
    row.version = payload.version or row.version
    row.plan = payload.plan or row.plan
    row.ip = ip or row.ip
    row.last_seen = _now()
    db.commit()
    return {"ok": True, "status": row.status}


class HeartbeatPayload(BaseModel):
    instanceId: str
    version: str | None = None
    meta: dict | None = None     # e.g. {"users": 12, "documents": 340}


@router.post("/sync/heartbeat")
def heartbeat(payload: HeartbeatPayload, db: Session = Depends(get_session),
              _t: None = Depends(require_fleet_token)) -> dict:
    row = db.get(AppInstance, payload.instanceId)
    if row is None:
        raise HTTPException(status_code=404, detail="unknown instance — register first")
    row.last_seen = _now()
    if payload.version:
        row.version = payload.version
    if payload.meta is not None:
        row.meta = payload.meta
    db.commit()
    return {"ok": True, "status": row.status}


@router.get("/sync/status/{instance_id}")
def status(instance_id: str, db: Session = Depends(get_session),
           _t: None = Depends(require_fleet_token)) -> dict:
    row = db.get(AppInstance, instance_id)
    if row is None:
        raise HTTPException(status_code=404, detail="not registered")
    return {"status": row.status, "plan": row.plan}


# ---- MEMBER side: register + heartbeat to the central instance ------------
async def member_loop() -> None:
    """Run on a MEMBER (dedicated container) when fleet_admin_url + instance_id +
    fleet_token are set: register once, then heartbeat forever. Best-effort —
    never crashes the app; just retries on the next interval."""
    import asyncio
    import logging

    import httpx

    log = logging.getLogger("docaiq.fleet")
    s = get_settings()
    base = s.fleet_admin_url.rstrip("/")
    headers = {"X-Fleet-Token": s.fleet_token, "Content-Type": "application/json"}

    async def _gather_meta() -> dict:
        try:
            from sqlalchemy import func, select
            from app.db import SessionLocal, set_current_tenant
            from app.orm import Document, User
            set_current_tenant(s.tenant_id)
            with SessionLocal() as db:
                users = int(db.scalar(select(func.count()).select_from(User)) or 0)
                docs = int(db.scalar(select(func.count()).select_from(Document)) or 0)
            return {"users": users, "documents": docs}
        except Exception:  # noqa: BLE001
            return {}

    try:
        async with httpx.AsyncClient(timeout=15) as cli:
            await cli.post(f"{base}/api/sync/register", headers=headers, json={
                "instanceId": s.instance_id, "name": s.instance_name or s.instance_id,
                "version": "0.1.0", "plan": "enterprise"})
            log.info("fleet: registered with %s as %s", base, s.instance_id)
    except Exception as e:  # noqa: BLE001
        log.warning("fleet: initial register failed: %s", e)

    while True:
        await asyncio.sleep(max(30, s.fleet_heartbeat_seconds))
        try:
            async with httpx.AsyncClient(timeout=15) as cli:
                await cli.post(f"{base}/api/sync/heartbeat", headers=headers, json={
                    "instanceId": s.instance_id, "version": "0.1.0", "meta": await _gather_meta()})
        except Exception as e:  # noqa: BLE001
            log.warning("fleet: heartbeat failed: %s", e)
