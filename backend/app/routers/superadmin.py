"""M47 · superadmin console (documents product).

Plan management across ALL users in the container: list users with their plan /
trial / usage, set a user's plan (free|pro|enterprise|trial), and extend a
trial. Gated to the emails in DOCAIQ_DOCUMENTS_SUPERADMIN_EMAILS — this is the
ONLY surface that reads across owners (every other documents query is scoped to
the calling owner), so the gate is strict.

Surfaced at admin.docaiq.jicama.tech in prod; locally reachable via the in-app
Admin tab when the signed-in user is a superadmin.
"""
from __future__ import annotations

import datetime as _dt

import json as _json

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Response
from pydantic import BaseModel
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_current_tenant, get_session
from app.orm import ProductFeedback, User
from app.security import CurrentUser, get_current_user
from app.services import subscriptions as subs

router = APIRouter()

_VALID_PLANS = {"trial", "free", "pro", "enterprise"}


def require_superadmin(
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> CurrentUser:
    """Gate the only cross-owner surface in the product.

    The JWT `email` claim is attacker-influenceable if the signing secret ever
    leaks, so we do NOT authorize on it directly. Instead we re-load the user
    row by its primary key and authorize on the DB email (and reject frozen
    accounts). The claim is now just a pointer; the source of truth is the DB.
    """
    s = get_settings()
    if s.product != "documents":
        raise HTTPException(status_code=404, detail="not found")
    row = db.get(User, user.id)
    if row is None or row.is_frozen:
        raise HTTPException(status_code=403, detail="superadmin only")
    email = (row.email or "").lower()
    # Env allowlist is the immutable bootstrap; the DB allowlist is the console-managed additions.
    if email not in s.superadmin_email_set and not _in_db_allow(db, row.tenant_id, email):
        raise HTTPException(status_code=403, detail="superadmin only")
    return user


def _in_db_allow(db: Session, tenant_id: str, email: str) -> bool:
    from app.orm import SuperadminAllow
    return db.scalar(select(SuperadminAllow).where(
        SuperadminAllow.tenant_id == tenant_id, SuperadminAllow.email == email)) is not None


def _now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


class PlanPayload(BaseModel):
    plan: str


class TrialPayload(BaseModel):
    days: int = subs.TRIAL_DAYS


def _user_row(db: Session, u: User, tenant_id: str) -> dict:
    return {
        "pk": u.pk,
        "email": u.email,
        "name": u.name,
        "plan": u.plan,
        "effectivePlan": subs.effective_plan(u),
        "trialEndsAt": u.trial_ends_at.isoformat() if u.trial_ends_at else None,
        "trialDaysLeft": subs.trial_days_left(u),
        "planSince": u.plan_since.isoformat() if u.plan_since else None,
        "createdAt": u.created_at.isoformat() if u.created_at else None,
        "docs": subs.doc_count(db, tenant_id=tenant_id, owner_user_id=u.pk),
        "aiThisMonth": subs.ai_messages_this_month(db, tenant_id=tenant_id, owner_user_id=u.pk),
        "lastUpload": _last_upload(db, tenant_id, u.pk),
    }


def _last_upload(db: Session, tenant_id: str, owner_user_id: int) -> str | None:
    from sqlalchemy import func as _func
    from app.orm import Document as _Doc
    ts = db.scalar(select(_func.max(_Doc.created_at)).where(
        _Doc.owner_user_id == owner_user_id, _Doc.tenant_id == tenant_id))
    return ts.isoformat() if ts else None


@router.get("/eval-corpus")
def eval_corpus_summary(db: Session = Depends(get_session),
                        _su: CurrentUser = Depends(require_superadmin)) -> dict:
    """Golden eval corpus coverage — total, verified, and per-doc-type breakdown.
    Captured from consented free-tier documents only."""
    from app.services import eval_corpus
    return eval_corpus.coverage(db, get_current_tenant())


@router.get("/eval-corpus.jsonl")
def eval_corpus_export(verifiedOnly: bool = Query(False),
                       db: Session = Depends(get_session),
                       _su: CurrentUser = Depends(require_superadmin)) -> Response:
    """Export the golden eval corpus as JSONL (one case per line) for the eval/
    harness. `verifiedOnly=1` restricts to human-confirmed ground-truth cases."""
    from app.services import eval_corpus
    cases = eval_corpus.export_cases(db, get_current_tenant(), verified_only=verifiedOnly)
    body = "\n".join(_json.dumps(c, ensure_ascii=False) for c in cases)
    return Response(content=body, media_type="application/x-ndjson",
                    headers={"Content-Disposition": "attachment; filename=golden_eval_corpus.jsonl"})


@router.get("/faithfulness")
def faithfulness_summary(db: Session = Depends(get_session),
                         _su: CurrentUser = Depends(require_superadmin)) -> dict:
    """Chat-faithfulness corpus coverage — total, labeled (👍/👎), abstained, and
    per-answer-path breakdown. Captured from consented free-tier chats only."""
    from app.services import faithfulness_corpus
    return faithfulness_corpus.coverage(db, get_current_tenant())


@router.get("/faithfulness.jsonl")
def faithfulness_export(labeledOnly: bool = Query(False),
                        db: Session = Depends(get_session),
                        _su: CurrentUser = Depends(require_superadmin)) -> Response:
    """Export the chat-faithfulness corpus as JSONL (Ragas/R4 shape: question · contexts
    · answer · groundTruth). `labeledOnly=1` → only human-labeled cases."""
    from app.services import faithfulness_corpus
    cases = faithfulness_corpus.export_cases(db, get_current_tenant(), labeled_only=labeledOnly)
    body = "\n".join(_json.dumps(c, ensure_ascii=False) for c in cases)
    return Response(content=body, media_type="application/x-ndjson",
                    headers={"Content-Disposition": "attachment; filename=faithfulness_corpus.jsonl"})


@router.get("/users")
def list_users(db: Session = Depends(get_session),
               _su: CurrentUser = Depends(require_superadmin)) -> dict:
    tid = get_current_tenant()
    rows = db.scalars(
        select(User).where(User.tenant_id == tid).order_by(User.created_at.desc())
    ).unique().all()
    return {
        "users": [_user_row(db, u, tid) for u in rows],
        "counts": _plan_counts(db, tid, rows),
    }


def _plan_counts(db: Session, tid: str, rows: list[User]) -> dict:
    out = {"trial": 0, "free": 0, "pro": 0, "enterprise": 0, "total": len(rows)}
    for u in rows:
        out[subs.effective_plan(u)] = out.get(subs.effective_plan(u), 0) + 1
    return out


@router.get("/activity")
def activity(db: Session = Depends(get_session),
             _su: CurrentUser = Depends(require_superadmin)) -> dict:
    """Aggregate usage for the Users & activity panel: registration, activation, docs,
    questions, and signups over the last 14 days."""
    from datetime import timedelta
    from sqlalchemy import func
    from app.orm import ChatMessage, Document

    tid = get_current_tenant()
    now = _now()

    def _c(q) -> int:
        return int(db.scalar(q) or 0)

    reg = _c(select(func.count()).select_from(User).where(User.tenant_id == tid))
    new7 = _c(select(func.count()).select_from(User).where(
        User.tenant_id == tid, User.created_at >= now - timedelta(days=7)))
    with_docs = _c(select(func.count(func.distinct(Document.owner_user_id))).where(
        Document.tenant_id == tid))
    active30 = _c(select(func.count(func.distinct(Document.owner_user_id))).where(
        Document.tenant_id == tid, Document.created_at >= now - timedelta(days=30)))
    total_docs = _c(select(func.count()).select_from(Document).where(Document.tenant_id == tid))
    total_q = _c(select(func.count()).select_from(ChatMessage).where(
        ChatMessage.tenant_id == tid, ChatMessage.role == "user"))
    rows = db.execute(
        select(func.date(User.created_at).label("d"), func.count())
        .where(User.tenant_id == tid, User.created_at >= now - timedelta(days=14))
        .group_by("d").order_by("d")
    ).all()
    return {
        "registered": reg,
        "newLast7d": new7,
        "usersWithDocs": with_docs,
        "activeLast30d": active30,
        "activationPct": round(100 * with_docs / reg) if reg else 0,
        "totalDocs": total_docs,
        "totalQuestions": total_q,
        "signupsByDay": [{"day": str(d), "n": int(n)} for d, n in rows],
    }


@router.post("/users/{pk}/plan")
def set_plan(pk: int, payload: PlanPayload, db: Session = Depends(get_session),
             _su: CurrentUser = Depends(require_superadmin)) -> dict:
    if payload.plan not in _VALID_PLANS:
        raise HTTPException(status_code=400, detail=f"plan must be one of {sorted(_VALID_PLANS)}")
    tid = get_current_tenant()
    u = db.get(User, pk)
    if u is None or u.tenant_id != tid:
        raise HTTPException(status_code=404, detail="user not found")
    u.plan = payload.plan
    u.plan_since = _now()
    if payload.plan == "trial":
        u.trial_ends_at = _now() + _dt.timedelta(days=subs.TRIAL_DAYS)
    db.commit()
    db.refresh(u)
    return _user_row(db, u, tid)


@router.post("/users/{pk}/trial")
def extend_trial(pk: int, payload: TrialPayload, db: Session = Depends(get_session),
                 _su: CurrentUser = Depends(require_superadmin)) -> dict:
    tid = get_current_tenant()
    u = db.get(User, pk)
    if u is None or u.tenant_id != tid:
        raise HTTPException(status_code=404, detail="user not found")
    base = u.trial_ends_at if (u.trial_ends_at and u.trial_ends_at > _now()) else _now()
    u.plan = "trial"
    # payload.days may be negative (admin shortening a long trial) — clamp the new
    # end at "now" so the trial can be reduced to 0 (expired) but never go negative.
    new_end = base + _dt.timedelta(days=payload.days)
    u.trial_ends_at = new_end if new_end > _now() else _now()
    u.plan_since = _now()
    db.commit()
    db.refresh(u)
    return _user_row(db, u, tid)


# ---- Promo codes (shareable, time-limited paid-plan grants) ----------------
class PromoCreatePayload(BaseModel):
    code: str
    plan: str = "pro"                 # pro | enterprise
    durationDays: int = 90
    maxRedemptions: int | None = None  # null = unlimited
    note: str | None = None


def _promo_row(r) -> dict:
    return {
        "pk": r.pk, "code": r.code, "plan": r.plan, "durationDays": r.duration_days,
        "maxRedemptions": r.max_redemptions, "redemptions": r.redemptions,
        "active": r.active, "note": r.note,
        "createdBy": r.created_by,
        "createdAt": r.created_at.isoformat() if r.created_at else None,
    }


@router.get("/promo-codes")
def list_promo(db: Session = Depends(get_session),
               _su: CurrentUser = Depends(require_superadmin)) -> dict:
    from app.orm import PromoCode
    rows = db.scalars(select(PromoCode).where(PromoCode.tenant_id == get_current_tenant())
                      .order_by(PromoCode.created_at.desc())).all()
    return {"codes": [_promo_row(r) for r in rows]}


@router.post("/promo-codes")
def create_promo(payload: PromoCreatePayload, db: Session = Depends(get_session),
                 su: CurrentUser = Depends(require_superadmin)) -> dict:
    from sqlalchemy import func as _func
    from app.orm import PromoCode
    tid = get_current_tenant()
    if payload.plan not in ("pro", "enterprise"):
        raise HTTPException(status_code=422, detail="plan must be 'pro' or 'enterprise'")
    code = (payload.code or "").strip()
    if not code:
        raise HTTPException(status_code=422, detail="code is required")
    if db.scalar(select(PromoCode).where(PromoCode.tenant_id == tid,
                                         _func.upper(PromoCode.code) == code.upper())):
        raise HTTPException(status_code=409, detail="A code with that name already exists")
    row = PromoCode(tenant_id=tid, code=code, plan=payload.plan,
                    duration_days=max(1, int(payload.durationDays)),
                    max_redemptions=payload.maxRedemptions, note=payload.note,
                    created_by=su.email)
    db.add(row)
    db.commit()
    db.refresh(row)
    return _promo_row(row)


@router.post("/promo-codes/{pk}/toggle")
def toggle_promo(pk: int, db: Session = Depends(get_session),
                 _su: CurrentUser = Depends(require_superadmin)) -> dict:
    from app.orm import PromoCode
    row = db.get(PromoCode, pk)
    if row is None or row.tenant_id != get_current_tenant():
        raise HTTPException(status_code=404, detail="code not found")
    row.active = not row.active
    db.commit()
    db.refresh(row)
    return _promo_row(row)


# ---- LLM model utilization (reads the bounded rollup, not the raw ledger) --
@router.get("/llm-usage")
def llm_usage(bucket: str = "day", scope: str = "tenant",
              db: Session = Depends(get_session),
              _su: CurrentUser = Depends(require_superadmin)) -> dict:
    """LLM utilization from the pre-aggregated llm_usage_rollup (small + bounded).
    bucket=day → last 30 days · bucket=month → one row per month (all-time).
    scope=tenant → per-model (all users combined) · scope=user → per-user."""
    from app.orm import LlmUsageRollup

    bucket = bucket if bucket in ("day", "month") else "day"
    scope = scope if scope in ("tenant", "user") else "tenant"
    tid = get_current_tenant()
    q = select(LlmUsageRollup).where(
        LlmUsageRollup.tenant_id == tid,
        LlmUsageRollup.period == bucket,
        (LlmUsageRollup.user_email != "") if scope == "user"
        else (LlmUsageRollup.user_email == ""),
    )
    buckets: set[str] = set()
    agg: dict[str, dict] = {}
    for r in db.scalars(q).all():
        biso = r.period_start.date().isoformat()
        buckets.add(biso)
        label = r.model if scope == "tenant" else r.user_email
        a = agg.setdefault(label, {"label": label,
                                   "provider": (r.provider if scope == "tenant" else None),
                                   "calls": 0, "inTok": 0, "outTok": 0, "series": {}})
        a["calls"] += r.calls
        a["inTok"] += r.input_tokens
        a["outTok"] += r.output_tokens
        s = a["series"].setdefault(biso, {"calls": 0, "inTok": 0, "outTok": 0})
        s["calls"] += r.calls
        s["inTok"] += r.input_tokens
        s["outTok"] += r.output_tokens
    return {"bucket": bucket, "scope": scope, "buckets": sorted(buckets),
            "rows": sorted(agg.values(), key=lambda x: -x["calls"])[:100]}


@router.post("/llm-usage/refresh")
def llm_usage_refresh(db: Session = Depends(get_session),
                      _su: CurrentUser = Depends(require_superadmin)) -> dict:
    """Rebuild the utilization rollup now (also runs hourly in the worker)."""
    from app.jobs.llm_rollup import refresh
    return {"ok": True, **refresh(db)}


@router.get("/llm-analytics")
def llm_analytics(days: int = 30,
                  db: Session = Depends(get_session),
                  _su: CurrentUser = Depends(require_superadmin)) -> dict:
    """Detailed LLM cost + usage straight from the `llm_calls` ledger (which carries cost_usd, task,
    model, provider, tokens, latency, status). Summary windows (today/7d/30d/month/all-time),
    breakdowns by model / task / provider, a daily cost time-series, and an average cost per document."""
    # P2 · cloud-only
    from app.license import is_cloud
    if not is_cloud():
        raise HTTPException(status_code=403, detail={
            "code": "cloud_feature",
            "message": "LLM cost analytics is a DocAIQ Cloud feature — not available on this OSS deployment.",
        })
    from app.orm import LLMCall, Document
    tid = get_current_tenant()
    now = _dt.datetime.utcnow()
    C = func.count()
    IN = func.coalesce(func.sum(LLMCall.input_tokens), 0)
    OUT = func.coalesce(func.sum(LLMCall.output_tokens), 0)
    COST = func.coalesce(func.sum(LLMCall.cost_usd), 0.0)

    def win(since):
        r = db.execute(select(C, COST, IN, OUT).where(
            LLMCall.tenant_id == tid, LLMCall.created_at >= since)).first()
        return {"calls": int(r[0]), "cost": round(float(r[1]), 4), "inTok": int(r[2]), "outTok": int(r[3])}

    d0 = _dt.datetime(now.year, now.month, now.day)
    summary = {
        "allTime": win(_dt.datetime(2000, 1, 1)),
        "today": win(d0),
        "last7": win(now - _dt.timedelta(days=7)),
        "last30": win(now - _dt.timedelta(days=30)),
        "month": win(_dt.datetime(now.year, now.month, 1)),
    }

    def grouped(col):
        rows = db.execute(
            select(col, C, COST, IN, OUT,
                   func.coalesce(func.avg(LLMCall.latency_ms), 0),
                   func.count().filter(LLMCall.status != "ok"))
            .where(LLMCall.tenant_id == tid).group_by(col).order_by(COST.desc())
        ).all()
        return [{"key": str(r[0] or "—"), "calls": int(r[1]), "cost": round(float(r[2]), 4),
                 "inTok": int(r[3]), "outTok": int(r[4]), "avgMs": int(r[5]), "errors": int(r[6])}
                for r in rows]

    since = now - _dt.timedelta(days=max(1, min(days, 180)))
    day = func.date_trunc("day", LLMCall.created_at)
    srows = db.execute(
        select(day, C, COST, (IN + OUT)).where(LLMCall.tenant_id == tid, LLMCall.created_at >= since)
        .group_by(day).order_by(day)
    ).all()
    series = [{"date": r[0].date().isoformat(), "calls": int(r[1]),
               "cost": round(float(r[2]), 4), "tok": int(r[3])} for r in srows]

    # Average cost per document = the extraction-family spend ÷ ready documents (a live estimate until
    # per-call document attribution accrues — see ledger.record_call(document_pk=…)).
    doc_cost = db.scalar(select(COST).where(
        LLMCall.tenant_id == tid,
        LLMCall.task_type.in_(("extract", "vision_extract", "classify", "extract_verify", "markdown", "ner")))) or 0.0
    ndocs = db.scalar(select(func.count()).select_from(Document).where(
        Document.tenant_id == tid, Document.ingestion_status == "ready")) or 0
    avg_per_doc = round(float(doc_cost) / ndocs, 4) if ndocs else 0.0

    # Exact per-document cost — accrues as new extractions record document_pk (migration 0097).
    drows = db.execute(
        select(LLMCall.document_pk, C, COST, (IN + OUT))
        .where(LLMCall.tenant_id == tid, LLMCall.document_pk.isnot(None))
        .group_by(LLMCall.document_pk).order_by(COST.desc()).limit(40)
    ).all()
    names = {}
    if drows:
        for pk, nm, ext in db.execute(select(Document.pk, Document.name, Document.id_external)
                                      .where(Document.pk.in_([r[0] for r in drows]))).all():
            names[pk] = (nm, ext)
    top_docs = [{"docId": (names.get(r[0]) or (None, None))[1],
                 "docName": (names.get(r[0]) or (f"doc#{r[0]}", None))[0] or f"doc#{r[0]}",
                 "calls": int(r[1]), "cost": round(float(r[2]), 4), "tok": int(r[3])} for r in drows]

    return {"summary": summary, "byModel": grouped(LLMCall.model), "byTask": grouped(LLMCall.task_type),
            "byProvider": grouped(LLMCall.provider), "series": series, "topDocs": top_docs,
            "avgPerDoc": avg_per_doc, "docs": int(ndocs), "docExtractionCost": round(float(doc_cost), 4)}


# ---- Partner / cross-tenant API clients (B2B keys — AuditAIQ-style) ---------
# Enterprise self-serve keys are minted by users in their own account (routers/keys.py). THESE are the
# partner keys DocAIQ issues to other apps: owner_user_id stays NULL (tenant/group-scoped, not one user).
_PARTNER_SCOPES = ["extract", "classify", "summarize", "ask", "documents:read",
                   "audit:match", "audit:ingest"]


class ApiClientPayload(BaseModel):
    name: str
    scopes: list[str] | None = None
    rateLimitRpm: int | None = None
    allowedGroupIds: list[int] | None = None


def _client_view(c) -> dict:
    return {"id": c.pk, "name": c.name, "keyPrefix": c.key_prefix, "scopes": c.scopes or [],
            "env": c.env, "rpm": c.rate_limit_rpm, "kind": "enterprise" if c.owner_user_id else "partner",
            "ownerUserId": c.owner_user_id, "allowedGroupIds": c.allowed_group_ids or [],
            "createdBy": c.created_by,
            "createdAt": c.created_at.isoformat() if c.created_at else None,
            "lastUsedAt": c.last_used_at.isoformat() if c.last_used_at else None,
            "revoked": c.revoked_at is not None}


@router.get("/api-clients")
def list_api_clients(db: Session = Depends(get_session),
                     _su: CurrentUser = Depends(require_superadmin)) -> dict:
    """Every API client — partner keys AND users' self-serve keys — for oversight."""
    from app.orm import ApiClient
    rows = db.scalars(select(ApiClient).order_by(ApiClient.pk.desc())).all()
    return {"clients": [_client_view(c) for c in rows], "scopes": _PARTNER_SCOPES}


@router.post("/api-clients")
def create_api_client(payload: ApiClientPayload, db: Session = Depends(get_session),
                      _su: CurrentUser = Depends(require_superadmin)) -> dict:
    """Mint a partner (cross-tenant) key. Raw key returned once."""
    from app import api_keys
    from app.orm import ApiClient
    raw = api_keys.generate_key("live")
    scopes = [s for s in (payload.scopes or ["extract", "ask", "documents:read"]) if s in _PARTNER_SCOPES]
    c = ApiClient(tenant_id=get_current_tenant(), owner_user_id=None,
                  name=(payload.name or "Partner key")[:128],
                  key_prefix=api_keys.key_prefix(raw), key_hash=api_keys.hash_key(raw), env="live",
                  scopes=scopes or ["extract"], rate_limit_rpm=int(payload.rateLimitRpm or 120),
                  allowed_group_ids=payload.allowedGroupIds or None, created_by=_su.email)
    db.add(c)
    db.commit()
    db.refresh(c)
    return {"key": raw, "client": _client_view(c),
            "warning": "Copy this key now — it will not be shown again."}


@router.delete("/api-clients/{client_id}")
def revoke_api_client(client_id: int, db: Session = Depends(get_session),
                      _su: CurrentUser = Depends(require_superadmin)) -> dict:
    """Revoke any API client (partner or self-serve) — takes effect on the next request."""
    from datetime import datetime, timezone
    from app.orm import ApiClient
    c = db.get(ApiClient, client_id)
    if c is None:
        raise HTTPException(status_code=404, detail="client not found")
    if c.revoked_at is None:
        c.revoked_at = datetime.now(timezone.utc)
        db.commit()
    return {"ok": True, "id": client_id}


# ---- Schema library (AI-architected, HITL-approved schemas per doc type) ---
class SchemaGenPayload(BaseModel):
    typeSlug: str
    label: str | None = None


class SchemaEditPayload(BaseModel):
    fields: dict | None = None
    notes: str | None = None
    label: str | None = None
    description: str | None = None


class SchemaStatusPayload(BaseModel):
    status: str   # approved | rejected | proposed


def _schema_row(r) -> dict:
    return {
        "pk": r.pk, "typeSlug": r.type_slug, "label": r.label, "domain": r.domain,
        "version": r.version, "status": r.status, "source": r.source, "model": r.model,
        "description": r.description, "fields": r.fields, "notes": r.notes,
        "fieldCount": len(r.fields or {}),
        "createdAt": r.created_at.isoformat() if r.created_at else None,
    }


def _store_drafted_schema(db, tid, slug, drafted, created_by, source="architect") -> "object":
    from app.orm import SchemaLibrary
    existing = db.scalars(select(SchemaLibrary).where(
        SchemaLibrary.tenant_id == tid, SchemaLibrary.type_slug == slug)).all()
    version = max([r.version for r in existing], default=0) + 1
    note = drafted.get("rationale") or ""
    conf = drafted.get("confidence")
    if conf is not None:
        note = f"[AI confidence {conf:.2f}] {note}".strip()
    row = SchemaLibrary(
        tenant_id=tid, type_slug=slug, label=drafted["label"], domain=drafted.get("domain"),
        version=version, fields=drafted["fields"], description=drafted.get("description"),
        status="proposed", source=source, model=drafted.get("model"), created_by=created_by,
        notes=note or None)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.get("/schema-library")
def list_schemas(db: Session = Depends(get_session),
                 _su: CurrentUser = Depends(require_superadmin)) -> dict:
    from app.orm import SchemaLibrary
    rows = db.scalars(select(SchemaLibrary).where(SchemaLibrary.tenant_id == get_current_tenant())
                      .order_by(SchemaLibrary.domain, SchemaLibrary.type_slug,
                                SchemaLibrary.version.desc())).all()
    return {"schemas": [_schema_row(r) for r in rows]}


@router.post("/schema-library/generate")
def generate_schema(payload: SchemaGenPayload, db: Session = Depends(get_session),
                    su: CurrentUser = Depends(require_superadmin)) -> dict:
    from app.license import is_cloud
    if not is_cloud():
        raise HTTPException(status_code=403, detail={
            "code": "cloud_feature",
            "message": "Schema Architect is a DocAIQ Cloud feature — not available on this OSS deployment.",
        })
    import re as _re
    from app.agents import schema_architect
    slug = _re.sub(r"[^a-z0-9_]+", "_", (payload.typeSlug or "").strip().lower()).strip("_")[:64]
    if not slug:
        raise HTTPException(status_code=422, detail="typeSlug required")
    try:
        drafted = schema_architect.draft_schema(db, type_slug=slug, label=payload.label)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Schema-Architect failed: {e}")
    row = _store_drafted_schema(db, get_current_tenant(), slug, drafted, su.email)
    return _schema_row(row)


@router.post("/schema-library/autopilot")
def run_schema_autopilot(background: BackgroundTasks, db: Session = Depends(get_session),
                         _su: CurrentUser = Depends(require_superadmin)) -> dict:
    from app.license import is_cloud
    if not is_cloud():
        raise HTTPException(status_code=403, detail={
            "code": "cloud_feature",
            "message": "Schema Autopilot is a DocAIQ Cloud feature — not available on this OSS deployment.",
        })
    """Adaptive Schema Loop — sweep the corpus for UNDERSERVED documents (no typed schema, or typed
    but poorly extracted) and auto-draft schemas for them (escalating to a stronger model). Drafts
    land `proposed` here for review; approving one re-extracts its docs. Runs in the background."""
    from app.queue import enqueue_schema_autopilot
    background.add_task(enqueue_schema_autopilot, get_current_tenant(), None)
    return {"status": "enqueued", "scope": "corpus-sweep"}


# ── Live QA tracker ─────────────────────────────────────────────────────────
# Server-persisted question status/issue/resolution (shared across the team) + a live "Run" that
# fires a question against the admin's own workspace chat. Seed bank is a Python module (app/data is
# .gitignore'd + .dockerignore'd, so a data file would silently vanish from the image).
def _qa_seed() -> dict:
    try:
        from app.qa_seed_data import SEED
        return SEED
    except Exception:  # noqa: BLE001
        return {"types": [], "questions": {}}


@router.get("/qa")
def qa_get(db: Session = Depends(get_session), _su: CurrentUser = Depends(require_superadmin)) -> dict:
    from app.orm import QaResult
    tid = get_current_tenant()
    rows = db.scalars(select(QaResult).where(QaResult.tenant_id == tid)).all()
    results = {r.qid: {"status": r.status, "issue": r.issue or "", "resolution": r.resolution or "",
                       "lastAnswer": r.last_answer or "", "custom": r.is_custom,
                       "question": r.question_text, "docType": r.doc_type} for r in rows}
    custom = [{"qid": r.qid, "question": r.question_text, "docType": r.doc_type}
              for r in rows if r.is_custom]
    return {"seed": _qa_seed(), "results": results, "custom": custom}


class QaSavePayload(BaseModel):
    status: str | None = None
    issue: str | None = None
    resolution: str | None = None
    questionText: str | None = None
    docType: str | None = None


@router.put("/qa/{qid}")
def qa_save(qid: str, payload: QaSavePayload, db: Session = Depends(get_session),
            su: CurrentUser = Depends(require_superadmin)) -> dict:
    from app.orm import QaResult
    tid = get_current_tenant()
    row = db.scalar(select(QaResult).where(QaResult.tenant_id == tid, QaResult.qid == qid))
    if row is None:
        row = QaResult(tenant_id=tid, qid=qid, question_text=payload.questionText, doc_type=payload.docType)
        db.add(row)
    if payload.status is not None:
        row.status = payload.status
    if payload.issue is not None:
        row.issue = payload.issue
    if payload.resolution is not None:
        row.resolution = payload.resolution
    if payload.questionText and not row.question_text:
        row.question_text = payload.questionText
    if payload.docType and not row.doc_type:
        row.doc_type = payload.docType
    row.updated_by = getattr(su, "email", None)
    db.commit()
    return {"ok": True}


class QaCustomPayload(BaseModel):
    docType: str
    question: str


@router.post("/qa/custom")
def qa_custom(payload: QaCustomPayload, db: Session = Depends(get_session),
              su: CurrentUser = Depends(require_superadmin)) -> dict:
    from app.orm import QaResult
    tid = get_current_tenant()
    qid = f"c{int(_now().timestamp() * 1000)}"
    db.add(QaResult(tenant_id=tid, qid=qid, question_text=payload.question, doc_type=payload.docType,
                    is_custom=True, updated_by=getattr(su, "email", None)))
    db.commit()
    return {"qid": qid, "docType": payload.docType, "question": payload.question}


@router.delete("/qa/{qid}")
def qa_delete(qid: str, db: Session = Depends(get_session),
              _su: CurrentUser = Depends(require_superadmin)) -> dict:
    from app.orm import QaResult
    tid = get_current_tenant()
    row = db.scalar(select(QaResult).where(QaResult.tenant_id == tid, QaResult.qid == qid))
    if row:
        db.delete(row)
        db.commit()
    return {"ok": True}


class QaRunPayload(BaseModel):
    qid: str | None = None
    question: str


@router.post("/qa/run")
def qa_run(payload: QaRunPayload, db: Session = Depends(get_session),
           su: CurrentUser = Depends(require_superadmin)) -> dict:
    """Run a question LIVE against the current admin's own workspace chat, and (if a qid is given)
    stash the answer on the QA row so it shows in the tracker."""
    from app.services import workspace_chat as wsc
    from app.documents_scope import set_current_owner_user_pk
    from app.orm import QaResult
    tid = get_current_tenant()
    set_current_owner_user_pk(su.id)
    try:
        res = wsc.post_message(db, tid, None, (payload.question or "").strip())
        ans = (res.get("text") if isinstance(res, dict) else str(res)) or ""
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"run failed: {e}")
    if payload.qid:
        row = db.scalar(select(QaResult).where(QaResult.tenant_id == tid, QaResult.qid == payload.qid))
        if row is None:
            row = QaResult(tenant_id=tid, qid=payload.qid, question_text=payload.question)
            db.add(row)
        row.last_answer = ans[:8000]
        db.commit()
    return {"answer": ans}


# ── Admin allowlist (console-managed superadmins, added to the env bootstrap) ─
@router.get("/admins")
def admins_list(db: Session = Depends(get_session),
                su: CurrentUser = Depends(require_superadmin)) -> dict:
    from app.orm import SuperadminAllow
    tid = get_current_tenant()
    rows = db.scalars(select(SuperadminAllow).where(SuperadminAllow.tenant_id == tid)
                      .order_by(SuperadminAllow.created_at)).all()
    me_row = db.get(User, su.id)
    return {
        "env": sorted(get_settings().superadmin_email_set),
        "db": [{"email": r.email, "addedBy": r.added_by,
                "createdAt": r.created_at.isoformat() if r.created_at else None} for r in rows],
        "me": (me_row.email or "").lower() if me_row else None,
    }


class AdminAddPayload(BaseModel):
    email: str


@router.post("/admins")
def admins_add(payload: AdminAddPayload, db: Session = Depends(get_session),
               su: CurrentUser = Depends(require_superadmin)) -> dict:
    import re as _re
    from app.orm import SuperadminAllow
    email = (payload.email or "").strip().lower()
    if not _re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        raise HTTPException(status_code=422, detail="Enter a valid email address")
    tid = get_current_tenant()
    if email in get_settings().superadmin_email_set:
        raise HTTPException(status_code=409, detail="Already an admin (set in the environment)")
    if db.scalar(select(SuperadminAllow).where(
            SuperadminAllow.tenant_id == tid, SuperadminAllow.email == email)):
        raise HTTPException(status_code=409, detail="Already an admin")
    me_row = db.get(User, su.id)
    db.add(SuperadminAllow(tenant_id=tid, email=email,
                           added_by=(me_row.email if me_row else None)))
    db.commit()
    return {"ok": True, "email": email}


@router.delete("/admins/{email}")
def admins_remove(email: str, db: Session = Depends(get_session),
                  _su: CurrentUser = Depends(require_superadmin)) -> dict:
    from app.orm import SuperadminAllow
    tid = get_current_tenant()
    email = email.strip().lower()
    if email in get_settings().superadmin_email_set:
        raise HTTPException(status_code=400,
                            detail="Set in the environment — can't be removed from the console.")
    row = db.scalar(select(SuperadminAllow).where(
        SuperadminAllow.tenant_id == tid, SuperadminAllow.email == email))
    if row:
        db.delete(row)
        db.commit()
    return {"ok": True}


class AdminPwPayload(BaseModel):
    newPassword: str


@router.post("/admins/{email}/password")
def admins_set_password(email: str, payload: AdminPwPayload, db: Session = Depends(get_session),
                        _su: CurrentUser = Depends(require_superadmin)) -> dict:
    """Set/reset the password for an ADMIN account (e.g. the shared admin@docaiq.jicama.tech) — a
    superadmin capability, so no current-password is required. Restricted to allowlisted admins."""
    from app.orm import User
    from app.auth import hash_password
    email = email.strip().lower()
    tid = get_current_tenant()
    if email not in get_settings().superadmin_email_set and not _in_db_allow(db, tid, email):
        raise HTTPException(status_code=403, detail="Only admin accounts can be managed here")
    if len(payload.newPassword or "") < 8:
        raise HTTPException(status_code=422, detail="Password must be at least 8 characters")
    u = db.scalar(select(User).where(User.email == email, User.tenant_id == tid))
    if u is None:
        raise HTTPException(status_code=404,
                            detail="That admin hasn't signed in yet — no account to set a password on")
    u.password_hash = hash_password(payload.newPassword)
    db.commit()
    return {"ok": True}


@router.post("/schema-library/{pk}/status")
def set_schema_status(pk: int, payload: SchemaStatusPayload, background: BackgroundTasks,
                      db: Session = Depends(get_session),
                      _su: CurrentUser = Depends(require_superadmin)) -> dict:
    from app.orm import SchemaLibrary
    if payload.status not in ("approved", "rejected", "proposed"):
        raise HTTPException(status_code=422, detail="bad status")
    row = db.get(SchemaLibrary, pk)
    if row is None or row.tenant_id != get_current_tenant():
        raise HTTPException(status_code=404, detail="not found")
    was_approved = row.status == "approved"
    row.status = payload.status
    db.commit()
    db.refresh(row)
    # On (re-)approval, auto-re-extract this type's docs so the typed schema is applied without a
    # manual backfill. Background so the approval call returns immediately; the worker does the work.
    if payload.status == "approved" and not was_approved:
        from app.queue import enqueue_reextract_type
        background.add_task(enqueue_reextract_type, row.type_slug, get_current_tenant())
    return _schema_row(row)


@router.patch("/schema-library/{pk}")
def edit_schema(pk: int, payload: SchemaEditPayload, db: Session = Depends(get_session),
                _su: CurrentUser = Depends(require_superadmin)) -> dict:
    from app.orm import SchemaLibrary
    row = db.get(SchemaLibrary, pk)
    if row is None or row.tenant_id != get_current_tenant():
        raise HTTPException(status_code=404, detail="not found")
    if payload.fields is not None:
        if not isinstance(payload.fields, dict) or not payload.fields:
            raise HTTPException(status_code=422, detail="fields must be a non-empty object")
        row.fields = payload.fields
    if payload.notes is not None:
        row.notes = payload.notes
    if payload.label is not None:
        row.label = payload.label
    if payload.description is not None:
        row.description = payload.description
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(row, "fields")
    db.commit()
    db.refresh(row)
    return _schema_row(row)


# ---- Product feedback review/resolve (the 'Send feedback' inbox) ----------
_FEEDBACK_STATUSES = {"new", "in_progress", "reviewed", "resolved", "verified"}


class FeedbackStatusPayload(BaseModel):
    status: str
    resolution: str | None = None   # optional resolution/triage note edit
    # Human review: accept-as-resolved is `status="verified"`; a reviewer who isn't
    # satisfied sets followupNeeded=true with a note (the endpoint re-opens the item).
    followupNeeded: bool | None = None
    followupNote: str | None = None


def _feedback_row(f: ProductFeedback) -> dict:
    return {
        "id": f.pk, "email": f.email, "rating": f.rating, "category": f.category,
        "comments": f.comments, "suggestion": f.suggestion, "page": f.page,
        "appVersion": f.app_version, "deviceInfo": f.device_info,
        "screenshots": f.screenshots if isinstance(f.screenshots, list) else [],
        "hasIssues": f.has_issues, "status": f.status, "resolution": f.resolution,
        "followupNeeded": f.followup_needed, "followupNote": f.followup_note,
        "ref": f.ref,
        "verifiedBy": f.verified_by,
        "verifiedAt": f.verified_at.isoformat() if f.verified_at else None,
        "createdAt": f.created_at.isoformat() if f.created_at else None,
        "reviewedAt": f.reviewed_at.isoformat() if f.reviewed_at else None,
    }


@router.get("/feedback")
def list_feedback(status: str | None = None, db: Session = Depends(get_session),
                  admin: CurrentUser = Depends(require_superadmin)) -> dict:
    tid = get_current_tenant()
    q = select(ProductFeedback).where(ProductFeedback.tenant_id == tid)
    if status:
        q = q.where(ProductFeedback.status == status)
    rows = db.scalars(q.order_by(desc(ProductFeedback.pk)).limit(500)).all()
    allrows = db.scalars(select(ProductFeedback).where(ProductFeedback.tenant_id == tid)).all()
    _done = sum(1 for r in allrows if r.status in ("resolved", "verified"))
    counts = {
        "total": len(allrows),
        "new": sum(1 for r in allrows if r.status == "new"),
        "inProgress": sum(1 for r in allrows if r.status == "in_progress"),
        "issues": sum(1 for r in allrows if r.has_issues and r.status not in ("resolved", "verified")),
        "resolved": sum(1 for r in allrows if r.status == "resolved"),
        "verified": sum(1 for r in allrows if r.status == "verified"),
        # Main version grows as feedback is resolved/verified (1.1.<#done>).
        "currentVersion": f"1.1.{_done}",
    }
    return {"feedback": [_feedback_row(r) for r in rows], "counts": counts}


@router.patch("/feedback/{pk}")
def set_feedback_status(pk: int, payload: FeedbackStatusPayload,
                        db: Session = Depends(get_session),
                        admin: CurrentUser = Depends(require_superadmin)) -> dict:
    if payload.status not in _FEEDBACK_STATUSES:
        raise HTTPException(status_code=400, detail=f"status must be one of {sorted(_FEEDBACK_STATUSES)}")
    f = db.scalar(select(ProductFeedback).where(
        ProductFeedback.pk == pk, ProductFeedback.tenant_id == get_current_tenant()))
    if f is None:
        raise HTTPException(status_code=404, detail="feedback not found")
    f.status = payload.status
    if payload.resolution is not None:
        f.resolution = payload.resolution.strip()[:2000] or None
    # Human review: a reviewer flags follow-up + leaves a note; accepting clears it.
    if payload.followupNeeded is not None:
        f.followup_needed = payload.followupNeeded
    if payload.followupNote is not None:
        f.followup_note = payload.followupNote.strip()[:2000] or None
    if payload.status == "verified":
        f.followup_needed = False   # accepting as resolved clears any open follow-up
    f.reviewed_at = None if payload.status == "new" else _now()
    if payload.status == "verified":
        # Verified by an admin → purge the screenshots (keep the row + all text/metadata).
        f.screenshots = None
        f.verified_by = admin.email
        f.verified_at = _now()
    db.commit()
    db.refresh(f)
    return _feedback_row(f)


# ---- Overview dashboard (KPIs + system health) ----------------------------
def _health(db: Session) -> dict:
    """Best-effort liveness of each dependency. Never raises."""
    s = get_settings()
    out: dict = {}
    try:
        db.execute(select(1))
        out["db"] = "ok"
    except Exception:  # noqa: BLE001
        out["db"] = "down"
    # Redis (shared by the worker).
    try:
        import redis as _r
        _r.from_url(s.redis_url, socket_connect_timeout=2).ping()
        out["redis"] = "ok"
        out["worker"] = "ok"
    except Exception:  # noqa: BLE001
        out["redis"] = "down"
        out["worker"] = "unknown"
    # Object storage (MinIO/S3).
    try:
        from app import storage
        storage._client().head_bucket(Bucket=s.s3_bucket)  # noqa: SLF001
        out["storage"] = "ok"
    except Exception:  # noqa: BLE001
        out["storage"] = "down"
    # LLM — which providers have a key configured.
    providers = {
        "openrouter": bool(s.openrouter_api_key), "dashscope": bool(s.dashscope_api_key),
        "anthropic": bool(s.anthropic_api_key), "google": bool(s.google_genai_api_key),
        "openai": bool(s.openai_api_key), "ollama": bool(s.ollama_base_url),
    }
    # Merge custom providers
    try:
        from app import llm_admin as _h_llm_admin
        for cp in _h_llm_admin.list_custom_providers(db):
            providers[cp["provider"]] = bool(cp["configured"])
    except Exception:  # noqa: BLE001 — health never raises
        pass
    out["llm"] = "ok" if any(providers.values()) else "unconfigured"
    out["llmProviders"] = providers
    return out


@router.get("/overview")
def overview(db: Session = Depends(get_session),
             admin: CurrentUser = Depends(require_superadmin)) -> dict:
    from app.orm import ChatMessage, Document, ProductFeedback
    s = get_settings()
    tid = get_current_tenant()
    month_start = _now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    users = list(db.scalars(select(User).where(User.tenant_id == tid)).unique().all())
    by_plan: dict[str, int] = {}
    for u in users:
        p = subs.effective_plan(u)
        by_plan[p] = by_plan.get(p, 0) + 1

    def _count(stmt):
        return int(db.scalar(stmt) or 0)

    docs_total = _count(select(func.count()).select_from(Document).where(Document.tenant_id == tid))
    docs_month = _count(select(func.count()).select_from(Document).where(
        Document.tenant_id == tid, Document.created_at >= month_start))
    docs_ready = _count(select(func.count()).select_from(Document).where(
        Document.tenant_id == tid, Document.ingestion_status == "ready"))
    docs_failed = _count(select(func.count()).select_from(Document).where(
        Document.tenant_id == tid, Document.ingestion_status == "failed"))
    ai_month = _count(select(func.count()).select_from(ChatMessage).where(
        ChatMessage.tenant_id == tid, ChatMessage.role == "user",
        ChatMessage.created_at >= month_start))
    fb_open = _count(select(func.count()).select_from(ProductFeedback).where(
        ProductFeedback.tenant_id == tid, ProductFeedback.status != "resolved"))

    return {
        "version": "0.1.0",
        "environment": s.environment,
        "tenant": tid,
        "kpis": {
            "users": len(users),
            "usersByPlan": by_plan,
            "documents": docs_total,
            "documentsThisMonth": docs_month,
            "documentsReady": docs_ready,
            "documentsFailed": docs_failed,
            "aiMessagesThisMonth": ai_month,
            "feedbackOpen": fb_open,
        },
        "health": _health(db),
        "timestamp": _now().isoformat(),
    }


# ---- Fleet · Enterprise dedicated-container instances ---------------------
_INSTANCE_ACTIONS = {"approve", "revoke", "pending"}


def _instance_row(i, offline_after: int) -> dict:
    age = (_now() - i.last_seen).total_seconds() if i.last_seen else None
    return {
        "instanceId": i.instance_id, "name": i.name, "hostname": i.hostname,
        "ip": i.ip, "version": i.version, "plan": i.plan, "status": i.status,
        "meta": i.meta or {},
        "online": (age is not None and age <= offline_after),
        "lastSeen": i.last_seen.isoformat() if i.last_seen else None,
        "firstSeen": i.first_seen.isoformat() if i.first_seen else None,
    }


@router.get("/instances")
def list_instances(db: Session = Depends(get_session),
                   admin: CurrentUser = Depends(require_superadmin)) -> dict:
    from app.orm import AppInstance
    s = get_settings()
    rows = db.scalars(select(AppInstance).order_by(desc(AppInstance.last_seen))).all()
    instances = [_instance_row(i, s.fleet_offline_after_seconds) for i in rows]
    return {
        "instances": instances,
        "counts": {
            "total": len(instances),
            "online": sum(1 for i in instances if i["online"]),
            "pending": sum(1 for i in instances if i["status"] == "pending"),
            "approved": sum(1 for i in instances if i["status"] == "approved"),
        },
        "fleetEnabled": bool(s.fleet_token),
    }


@router.post("/instances/{instance_id}/{action}")
def instance_action(instance_id: str, action: str,
                    db: Session = Depends(get_session),
                    admin: CurrentUser = Depends(require_superadmin)) -> dict:
    from app.orm import AppInstance
    if action not in _INSTANCE_ACTIONS:
        raise HTTPException(status_code=400, detail=f"action must be one of {sorted(_INSTANCE_ACTIONS)}")
    i = db.get(AppInstance, instance_id)
    if i is None:
        raise HTTPException(status_code=404, detail="instance not found")
    i.status = "approved" if action == "approve" else ("revoked" if action == "revoke" else "pending")
    db.commit()
    return _instance_row(i, get_settings().fleet_offline_after_seconds)


# ---- LLM provider config (key / enable / model / probe) -------------------
class LlmProviderPayload(BaseModel):
    enabled: bool | None = None
    apiKey: str | None = None       # paste to set/replace; encrypted at rest
    clearKey: bool | None = None    # remove the DB override → fall back to env
    defaultModel: str | None = None
    label: str | None = None        # custom providers only
    baseUrl: str | None = None      # custom providers only


class CustomLlmProviderPayload(BaseModel):
    slug: str
    label: str
    baseUrl: str
    apiKey: str | None = None
    enabled: bool = True
    defaultModel: str | None = None


def _custom_providers_dict(db: Session) -> dict[str, dict]:
    """Load custom providers for threading through model_registry functions."""
    from app import llm_admin
    return {p["provider"]: p for p in llm_admin.list_custom_providers(db)}


@router.get("/llm")
def get_llm(db: Session = Depends(get_session),
            admin: CurrentUser = Depends(require_superadmin)) -> dict:
    from app import llm_admin
    return {"providers": llm_admin.list_providers(db)}


@router.post("/llm/custom")
def create_custom_llm(payload: CustomLlmProviderPayload,
                      db: Session = Depends(get_session),
                      admin: CurrentUser = Depends(require_superadmin)) -> dict:
    from app import llm_admin
    try:
        return llm_admin.set_custom_provider(
            db, payload.slug, label=payload.label, base_url=payload.baseUrl,
            api_key=payload.apiKey, enabled=payload.enabled,
            default_model=payload.defaultModel, create=True)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.patch("/llm/{provider}")
def update_llm(provider: str, payload: LlmProviderPayload,
               db: Session = Depends(get_session),
               admin: CurrentUser = Depends(require_superadmin)) -> dict:
    from app import llm_admin
    try:
        if provider in llm_admin.PROVIDER_KEY_ATTR:
            return llm_admin.set_provider(
                db, provider, enabled=payload.enabled, api_key=payload.apiKey,
                default_model=payload.defaultModel, clear_key=bool(payload.clearKey))
        # Custom provider
        if llm_admin.get_custom_provider(db, provider) is not None:
            return llm_admin.set_custom_provider(
                db, provider, label=payload.label, base_url=payload.baseUrl,
                api_key=payload.apiKey, clear_key=bool(payload.clearKey),
                enabled=payload.enabled, default_model=payload.defaultModel)
        raise HTTPException(status_code=404, detail=f"unknown provider: {provider!r}")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/llm/{provider}")
def delete_llm(provider: str, db: Session = Depends(get_session),
               admin: CurrentUser = Depends(require_superadmin)) -> dict:
    from app import llm_admin
    if provider in llm_admin.PROVIDER_KEY_ATTR:
        raise HTTPException(status_code=400, detail="built-in providers cannot be deleted")
    try:
        llm_admin.delete_custom_provider(db, provider)
        return {"provider": provider, "deleted": True}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/llm/{provider}/probe")
def probe_llm(provider: str, db: Session = Depends(get_session),
              admin: CurrentUser = Depends(require_superadmin)) -> dict:
    from app import llm_admin
    if provider not in llm_admin.PROVIDER_KEY_ATTR and llm_admin.get_custom_provider(db, provider) is None:
        raise HTTPException(status_code=400, detail="unknown provider")
    return llm_admin.probe(db, provider)


# ---- AI Operations registry (model → operation mapping) -------------------
class AiOperationOverride(BaseModel):
    model: str | None = None
    reset: bool | None = None  # True → revert to env default


@router.get("/ai-operations")
def get_ai_operations(db: Session = Depends(get_session),
                      admin: CurrentUser = Depends(require_superadmin)) -> dict:
    """Return every AI operation with its effective model, grouped by category.
    Includes per-provider key status so the admin console can show which
    operations have working credentials."""
    from app.model_registry import get_operations_by_category, get_providers_status
    from app.repositories import routing_configs as rc_repo
    rc = rc_repo.get(db) or {}
    overrides = rc.get("operations", {})
    custom = _custom_providers_dict(db)
    return {
        "providers": get_providers_status(custom),
        "categories": get_operations_by_category(overrides, custom),
    }


@router.patch("/ai-operations/{op_id}")
def update_ai_operation(op_id: str, payload: AiOperationOverride,
                        db: Session = Depends(get_session),
                        admin: CurrentUser = Depends(require_superadmin)) -> dict:
    """Override the model used for a specific operation (admin-only).
    Stored in routing_config.operations — survives restarts.
    Send {reset: true} to revert to the env-var / hardcoded default."""
    from app.model_registry import REGISTRY, resolve_model
    from app.repositories import routing_configs as rc_repo

    if op_id not in REGISTRY:
        raise HTTPException(status_code=404, detail=f"unknown operation: {op_id!r}")
    op = REGISTRY[op_id]
    if not op.editable:
        raise HTTPException(status_code=403, detail=f"operation {op_id!r} is not editable")

    rc = rc_repo.get(db) or {}
    ops = dict(rc.get("operations", {}))
    if payload.reset:
        ops.pop(op_id, None)
    elif payload.model:
        ops[op_id] = {"model": payload.model}
    else:
        raise HTTPException(status_code=400, detail="set 'model' or 'reset: true'")
    rc["operations"] = ops
    rc_repo.upsert(db, rc)

    return {
        "id": op_id,
        "model": resolve_model(op_id, ops),
        "overridden": op_id in ops,
    }


# ---- Provider model list management (add/remove models per provider) ------
class ProviderModelPayload(BaseModel):
    model: str


@router.get("/provider-models")
def get_provider_models_ep(db: Session = Depends(get_session),
                           admin: CurrentUser = Depends(require_superadmin)) -> dict:
    """Return every provider's available model list (defaults + admin custom entries)."""
    from app.model_registry import get_provider_models
    from app.repositories import routing_configs as rc_repo
    rc = rc_repo.get(db) or {}
    custom_models = rc.get("provider_models", {})
    return {"providers": get_provider_models(custom_models, _custom_providers_dict(db))}


def _is_known_provider(db: Session, provider: str) -> bool:
    """True if the provider slug is a hardcoded or registered custom provider."""
    from app.model_registry import PROVIDERS as MR_PROVIDERS
    from app import llm_admin
    return provider in MR_PROVIDERS or llm_admin.get_custom_provider(db, provider) is not None


@router.post("/provider-models/{provider}")
def add_provider_model(provider: str, payload: ProviderModelPayload,
                       db: Session = Depends(get_session),
                       admin: CurrentUser = Depends(require_superadmin)) -> dict:
    """Add a model name to a provider's available model list."""
    from app.model_registry import PROVIDERS
    from app.repositories import routing_configs as rc_repo
    model = (payload.model or "").strip()
    if not model:
        raise HTTPException(status_code=400, detail="model name is required")
    if not _is_known_provider(db, provider):
        raise HTTPException(status_code=404, detail=f"unknown provider: {provider!r}")
    rc = rc_repo.get(db) or {}
    pm = rc.get("provider_models", {})
    cur = list(pm.get(provider, []))
    if model in cur:
        return {"provider": provider, "models": cur, "message": "already present"}
    cur.append(model)
    pm[provider] = cur
    rc["provider_models"] = pm
    rc_repo.upsert(db, rc)
    return {"provider": provider, "model": model, "models": cur}


@router.delete("/provider-models/{provider}/{model:path}")
def delete_provider_model(provider: str, model: str,
                          db: Session = Depends(get_session),
                          admin: CurrentUser = Depends(require_superadmin)) -> dict:
    """Remove a custom model from a provider's list. Cannot remove hardcoded defaults."""
    from app.model_registry import PROVIDERS
    from app.repositories import routing_configs as rc_repo
    model = model.strip()
    if not _is_known_provider(db, provider):
        raise HTTPException(status_code=404, detail=f"unknown provider: {provider!r}")
    # Hardcoded defaults can only be checked for known PROVIDERS entries
    if provider in PROVIDERS:
        defaults = list(PROVIDERS[provider].models)
        if model in defaults:
            raise HTTPException(status_code=400, detail=f"{model!r} is a built-in default for {provider} — cannot delete")
    rc = rc_repo.get(db) or {}
    pm = rc.get("provider_models", {})
    cur = list(pm.get(provider, []))
    if model not in cur:
        raise HTTPException(status_code=404, detail=f"custom model {model!r} not found for {provider}")
    cur.remove(model)
    if cur:
        pm[provider] = cur
    else:
        pm.pop(provider, None)
    rc["provider_models"] = pm
    rc_repo.upsert(db, rc)
    return {"provider": provider, "model": model, "models": cur}


# ── PII redaction config ─────────────────────────────────────────────────
# Stored in routing_config.config.pii alongside operations + provider_models.
# Controls which PII categories the LLM gateway masks before sending to
# external providers. Admin can toggle the whole thing + per-category groups.

PII_DEFAULTS: dict = {
    "enabled": True,
    "categories": {
        "dates":     False,   # off by default — dates are extraction data
        "names":     False,   # off by default — names are the search key
        "contact":   True,
        "govt_ids":  True,
        "financial": True,
        "network":   True,
    },
}


def _get_pii_config(db: Session) -> dict:
    """Merge tenant PII config with defaults. Returns the effective config."""
    from app.repositories import routing_configs as rc_repo
    rc = rc_repo.get(db) or {}
    stored = rc.get("pii", {})
    merged = dict(PII_DEFAULTS)  # shallow copy
    if isinstance(stored, dict):
        merged["enabled"] = stored.get("enabled", PII_DEFAULTS["enabled"])
        stored_cats = stored.get("categories", {}) if isinstance(stored.get("categories"), dict) else {}
        merged["categories"] = {
            k: stored_cats.get(k, PII_DEFAULTS["categories"].get(k, True))
            for k in PII_DEFAULTS["categories"]
        }
    return merged


class PiiCategoryUpdate(BaseModel):
    dates: bool | None = None
    names: bool | None = None
    contact: bool | None = None
    govt_ids: bool | None = None
    financial: bool | None = None
    network: bool | None = None


class PiiConfigUpdate(BaseModel):
    enabled: bool | None = None
    categories: PiiCategoryUpdate | None = None


@router.get("/pii-config")
def get_pii_config(db: Session = Depends(get_session),
                   admin: CurrentUser = Depends(require_superadmin)) -> dict:
    """Return the current PII redaction config (from routing_config)."""
    return _get_pii_config(db)


@router.patch("/pii-config")
def update_pii_config(payload: PiiConfigUpdate,
                      db: Session = Depends(get_session),
                      admin: CurrentUser = Depends(require_superadmin)) -> dict:
    """Update PII redaction config. Stored in routing_config — survives restarts."""
    from app.repositories import routing_configs as rc_repo
    rc = rc_repo.get(db) or {}
    current = _get_pii_config(db)

    if payload.enabled is not None:
        current["enabled"] = payload.enabled
    if payload.categories is not None:
        cats = dict(current["categories"])
        updates = payload.categories.model_dump(exclude_none=True)
        for k, v in updates.items():
            if v is not None:
                cats[k] = v
        current["categories"] = cats

    rc["pii"] = current
    rc_repo.upsert(db, rc)
    return current


# ── Helper for gateway ──────────────────────────────────────────────────
# Module-level cache so the gateway doesn't hit the DB on every LLM call.
# Same pattern as the custom-provider cache in llm_admin.

_pii_config_cache: dict | None = None


def _refresh_pii_config_cache(db: Session) -> dict:
    """Update the in-memory PII config cache. Called after config changes
    AND once per gateway call when the cache is cold."""
    global _pii_config_cache
    _pii_config_cache = _get_pii_config(db)
    return _pii_config_cache


def get_cached_pii_config() -> dict:
    """Return the effective PII config from the in-memory cache.
    Returns defaults if the cache hasn't been populated yet (boot / no DB access)."""
    if _pii_config_cache is not None:
        return _pii_config_cache
    return dict(PII_DEFAULTS)


def invalidate_pii_config_cache() -> None:
    """Clear the in-memory cache so the next gateway call re-reads from DB."""
    global _pii_config_cache
    _pii_config_cache = None


# ── Embedding config (admin-controllable backend + model) ──────────────────
# Stored in routing_config.config.embedding: {v1_backend, v2_backend, v2_active}.
# The embedding module reads from this via its own module-level cache.

EMBEDDING_DEFAULTS: dict = {
    "v1_backend": "",
    "v2_backend": "",
    "v2_active": True,
}

EMBEDDING_BACKENDS_V1 = ["local", "hash", "dashscope", "openai", "gemini"]
EMBEDDING_BACKENDS_V2 = ["local", "dashscope"]


def _get_embedding_config(db: Session) -> dict:
    """Merge tenant embedding config with defaults."""
    from app.repositories import routing_configs as rc_repo
    rc = rc_repo.get(db) or {}
    stored = rc.get("embedding", {})
    merged = dict(EMBEDDING_DEFAULTS)
    if isinstance(stored, dict):
        merged["v1_backend"] = stored.get("v1_backend", EMBEDDING_DEFAULTS["v1_backend"])
        merged["v2_backend"] = stored.get("v2_backend", EMBEDDING_DEFAULTS["v2_backend"])
        merged["v2_active"] = stored.get("v2_active", EMBEDDING_DEFAULTS["v2_active"])
    return merged


class EmbeddingConfigUpdate(BaseModel):
    v1_backend: str | None = None
    v2_backend: str | None = None
    v2_active: bool | None = None


@router.get("/embedding-config")
def get_embedding_config(db: Session = Depends(get_session),
                         admin: CurrentUser = Depends(require_superadmin)) -> dict:
    """Return the current embedding backend config (from routing_config)."""
    return _get_embedding_config(db)


@router.patch("/embedding-config")
def update_embedding_config(payload: EmbeddingConfigUpdate,
                            db: Session = Depends(get_session),
                            admin: CurrentUser = Depends(require_superadmin)) -> dict:
    """Update embedding backend config. Stored in routing_config — survives restarts.
    ⚠️ Changing the backend requires re-indexing all documents."""
    from app.repositories import routing_configs as rc_repo
    rc = rc_repo.get(db) or {}
    current = _get_embedding_config(db)

    if payload.v1_backend is not None:
        if payload.v1_backend not in EMBEDDING_BACKENDS_V1:
            raise HTTPException(400, f"Unknown v1 backend: {payload.v1_backend!r}. "
                                     f"Valid: {EMBEDDING_BACKENDS_V1}")
        current["v1_backend"] = payload.v1_backend
    if payload.v2_backend is not None:
        if payload.v2_backend not in EMBEDDING_BACKENDS_V2:
            raise HTTPException(400, f"Unknown v2 backend: {payload.v2_backend!r}. "
                                     f"Valid: {EMBEDDING_BACKENDS_V2}")
        current["v2_backend"] = payload.v2_backend
    if payload.v2_active is not None:
        current["v2_active"] = payload.v2_active

    rc["embedding"] = current
    rc_repo.upsert(db, rc)

    # Invalidate the embeddings module cache so it picks up the change
    try:
        from app.embeddings import invalidate_embedding_config_cache
        invalidate_embedding_config_cache()
    except Exception:
        pass

    return current


# ── Feature flags ──────────────────────────────────────────────────────────
# Stored in routing_config.config.features: {flag_name: value}.
# The feature_flags module reads from this via its own module-level cache.

class FeatureFlagUpdate(BaseModel):
    updates: dict  # {flag_name: new_value}


@router.get("/feature-flags")
def get_feature_flags(db: Session = Depends(get_session),
                      admin: CurrentUser = Depends(require_superadmin)) -> list[dict]:
    """Return all feature flags with current effective values."""
    from app.feature_flags import get_all_flags, _refresh_feature_flags_cache
    _refresh_feature_flags_cache(db)
    return get_all_flags()


@router.patch("/feature-flags")
def update_feature_flags(payload: FeatureFlagUpdate,
                         db: Session = Depends(get_session),
                         admin: CurrentUser = Depends(require_superadmin)) -> dict:
    """Update one or more feature flags. Stored in routing_config — survives restarts."""
    from app.repositories import routing_configs as rc_repo
    from app.feature_flags import FEATURE_FLAGS, invalidate_feature_flags_cache
    rc = rc_repo.get(db) or {}
    features = dict(rc.get("features", {})) if isinstance(rc.get("features"), dict) else {}

    valid_names = {f["name"] for f in FEATURE_FLAGS}
    for name, value in payload.updates.items():
        if name not in valid_names:
            raise HTTPException(400, f"Unknown feature flag: {name!r}")
        flag_def = next(f for f in FEATURE_FLAGS if f["name"] == name)
        if flag_def["type"] == "bool" and not isinstance(value, bool):
            raise HTTPException(400, f"Flag {name!r} expects a boolean value")
        if flag_def["type"] == "int" and not isinstance(value, int):
            raise HTTPException(400, f"Flag {name!r} expects an integer value")
        features[name] = value

    rc["features"] = features
    rc_repo.upsert(db, rc)
    invalidate_feature_flags_cache()
    return {"ok": True, "updated": list(payload.updates.keys())}


# ── Per-user utilization (Usage tab) ───────────────────────────────────
@router.get("/user-usage")
def get_user_usage(db: Session = Depends(get_session),
                   admin: CurrentUser = Depends(require_superadmin)) -> dict:
    """Per-user aggregated utilization: docs, pages, LLM usage, cost, chat, edits.

    Returns a list of user rows with all metrics, plus a summary KPIs block.
    """
    from datetime import datetime, timezone
    from sqlalchemy import func, text
    from app.orm import Document, User, LLMCall, ChatMessage

    tenant_id = get_current_tenant()
    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    # ── Users in this tenant ──────────────────────────────────────────
    users = {
        u.pk: {"pk": u.pk, "email": u.email or "", "name": u.name or "",
               "plan": u.plan or "free", "createdAt": u.created_at.isoformat() if u.created_at else None}
        for u in db.query(User).where(User.tenant_id == tenant_id).all()
    }
    if not users:
        return {"users": [], "summary": {"totalUsers": 0}}

    user_ids = list(users.keys())
    uid_by_email = {u["email"]: pk for pk, u in users.items()}

    # ── Document counts per owner ──────────────────────────────────────
    doc_rows = db.execute(
        select(
            Document.owner_user_id,
            func.count(Document.pk),
            func.sum(func.coalesce(Document.pages, 0)),
            func.count(Document.pk).filter(Document.ingestion_status == "ready"),
            func.count(Document.pk).filter(Document.ingestion_status == "failed"),
            func.count(Document.pk).filter(Document.created_at >= month_start),
            func.count(Document.pk).filter(Document.rendered_markdown.isnot(None)),
        ).where(
            Document.owner_user_id.in_(user_ids),
            Document.tenant_id == tenant_id,
            Document.is_archived == False,
        ).group_by(Document.owner_user_id)
    ).all()

    for row in doc_rows:
        uid, total, pages, ready, failed, month, edited = row
        u = users.get(uid)
        if u:
            u.update(docsTotal=int(total), pagesTotal=int(pages or 0),
                     docsReady=int(ready), docsFailed=int(failed),
                     docsThisMonth=int(month), docsEdited=int(edited))

    # ── LLM usage per owner (via document_pk join) ─────────────────────
    llm_rows = db.execute(
        select(
            Document.owner_user_id,
            func.count(LLMCall.pk),
            func.sum(func.coalesce(LLMCall.input_tokens, 0)),
            func.sum(func.coalesce(LLMCall.output_tokens, 0)),
            func.sum(func.coalesce(LLMCall.cost_usd, 0.0)),
        ).join(Document, LLMCall.document_pk == Document.pk).where(
            Document.owner_user_id.in_(user_ids),
            Document.tenant_id == tenant_id,
            LLMCall.tenant_id == tenant_id,
        ).group_by(Document.owner_user_id)
    ).all()

    for row in llm_rows:
        uid, calls, inp, outp, cost = row
        u = users.get(uid)
        if u:
            u.update(llmCalls=int(calls), llmInputTokens=int(inp or 0),
                     llmOutputTokens=int(outp or 0), llmCost=round(float(cost or 0), 6))

    # ── Chat questions per owner ───────────────────────────────────────
    # Doc-chat: join via doc_id_external → documents.id_external
    chat_rows = db.execute(
        select(
            Document.owner_user_id,
            func.count(ChatMessage.pk),
        ).join(Document, ChatMessage.doc_id_external == Document.id_external).where(
            Document.owner_user_id.in_(user_ids),
            Document.tenant_id == tenant_id,
            ChatMessage.role == "user",
            ChatMessage.tenant_id == tenant_id,
        ).group_by(Document.owner_user_id)
    ).all()

    for row in chat_rows:
        uid, questions = row
        u = users.get(uid)
        if u:
            u["chatQuestions"] = int(questions)

    # Also count workspace-chat questions (workspace_key = 'user:{pk}')
    ws_rows = db.execute(
        select(
            ChatMessage.workspace_key,
            func.count(ChatMessage.pk),
        ).where(
            ChatMessage.role == "user",
            ChatMessage.tenant_id == tenant_id,
            ChatMessage.workspace_key.startswith("user:"),
        ).group_by(ChatMessage.workspace_key)
    ).all()

    for ws_key, count in ws_rows:
        try:
            uid = int(ws_key.split(":", 1)[1])
        except (ValueError, IndexError):
            continue
        u = users.get(uid)
        if u:
            u["chatQuestions"] = u.get("chatQuestions", 0) + int(count)

    # ── Reprocessed docs per owner (docs with >1 ingestion LLM calls) ──
    repro_rows = db.execute(
        select(
            Document.owner_user_id,
            func.count(Document.pk.distinct()),
        ).join(LLMCall, LLMCall.document_pk == Document.pk).where(
            Document.owner_user_id.in_(user_ids),
            Document.tenant_id == tenant_id,
            LLMCall.task_type == "ingestion",
            LLMCall.tenant_id == tenant_id,
        ).group_by(Document.owner_user_id, LLMCall.document_pk)
         .having(func.count(LLMCall.pk) > 1)
    ).all()
    # The above query returns one row per (owner, doc) — count per owner
    repro_by_owner: dict[int, int] = {}
    for row in repro_rows:
        uid = row[0]
        repro_by_owner[uid] = repro_by_owner.get(uid, 0) + 1
    for uid, count in repro_by_owner.items():
        u = users.get(uid)
        if u:
            u["docsReprocessed"] = count

    # ── Last active ────────────────────────────────────────────────────
    last_rows = db.execute(
        select(
            Document.owner_user_id,
            func.max(Document.created_at),
        ).where(
            Document.owner_user_id.in_(user_ids),
            Document.tenant_id == tenant_id,
        ).group_by(Document.owner_user_id)
    ).all()
    for uid, ts in last_rows:
        u = users.get(uid)
        if u:
            u["lastActive"] = ts.isoformat() if ts else None

    # ── Build result ───────────────────────────────────────────────────
    result = []
    for u in users.values():
        result.append({
            "email": u["email"],
            "name": u["name"],
            "plan": u["plan"],
            "docsTotal": u.get("docsTotal", 0),
            "docsReady": u.get("docsReady", 0),
            "docsFailed": u.get("docsFailed", 0),
            "docsThisMonth": u.get("docsThisMonth", 0),
            "pagesTotal": u.get("pagesTotal", 0),
            "llmCalls": u.get("llmCalls", 0),
            "llmInputTokens": u.get("llmInputTokens", 0),
            "llmOutputTokens": u.get("llmOutputTokens", 0),
            "llmCost": u.get("llmCost", 0),
            "chatQuestions": u.get("chatQuestions", 0),
            "docsEdited": u.get("docsEdited", 0),
            "docsReprocessed": u.get("docsReprocessed", 0),
            "lastActive": u.get("lastActive"),
        })
    result.sort(key=lambda r: r["llmCost"], reverse=True)

    # Summary KPIs
    return {
        "users": result,
        "summary": {
            "totalUsers": len(result),
            "totalDocs": sum(r["docsTotal"] for r in result),
            "totalTokens": sum(r["llmInputTokens"] + r["llmOutputTokens"] for r in result),
            "totalCost": round(sum(r["llmCost"] for r in result), 6),
            "totalChatQuestions": sum(r["chatQuestions"] for r in result),
        },
    }


# ---- Plan configuration (enable/disable + quotas + LLM + dedicated) -------
class PlanConfigPayload(BaseModel):
    enabled: bool | None = None
    docsMonthly: int | None = None          # null in body = "no change"; see clearDocs
    aiMonthly: int | None = None
    paidModels: bool | None = None
    llmEnabled: bool | None = None
    dedicatedContainer: bool | None = None
    features: list[str] | None = None
    # Explicit "set to unlimited" toggles (since null means no-change above).
    unlimitedDocs: bool | None = None
    unlimitedAi: bool | None = None


@router.get("/plans")
def get_plans(db: Session = Depends(get_session),
              admin: CurrentUser = Depends(require_superadmin)) -> dict:
    return {"plans": subs.list_plan_configs(db)}


@router.patch("/plans/{plan}")
def update_plan(plan: str, payload: PlanConfigPayload,
                db: Session = Depends(get_session),
                admin: CurrentUser = Depends(require_superadmin)) -> dict:
    """Update a plan's config. Only the fields present in the body change. For the
    quotas: send a number to cap, or unlimitedDocs/unlimitedAi=true for no cap."""
    fields: dict = {}
    for src in ("enabled", "paidModels", "llmEnabled", "dedicatedContainer", "features"):
        v = getattr(payload, src)
        if v is not None:
            fields[src] = v
    if payload.unlimitedDocs:
        fields["docsMonthly"] = None
    elif payload.docsMonthly is not None:
        fields["docsMonthly"] = payload.docsMonthly
    if payload.unlimitedAi:
        fields["aiMonthly"] = None
    elif payload.aiMonthly is not None:
        fields["aiMonthly"] = payload.aiMonthly
    try:
        return subs.set_plan_config(db, plan, tenant_id=get_current_tenant(), **fields)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ── Scoped reprocessing ─────────────────────────────────────────────────────
# After a root-cause parse/extract fix lands, find every affected doc across ALL
# owners and re-run the pipeline on just those (not a blind whole-corpus rerun).
# See services/reprocess.py.

class ReprocessScanPayload(BaseModel):
    symptom: str
    docType: str | None = None


class ReprocessRunPayload(BaseModel):
    symptom: str | None = None
    docType: str | None = None
    docPks: list[int] | None = None
    limit: int = 500


@router.get("/reprocess/symptoms")
def reprocess_symptoms(_su: CurrentUser = Depends(require_superadmin)) -> dict:
    from app.services import reprocess
    return {"symptoms": reprocess.list_symptoms()}


@router.post("/reprocess/scan")
def reprocess_scan(payload: ReprocessScanPayload,
                   db: Session = Depends(get_session),
                   _su: CurrentUser = Depends(require_superadmin)) -> dict:
    """Read-only. Count + sample the documents (across all owners) that still show
    the given symptom, so an operator can review before re-running the pipeline."""
    from app.services import reprocess
    try:
        return reprocess.scan(db, get_current_tenant(), payload.symptom, payload.docType)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/reprocess/run")
async def reprocess_run(payload: ReprocessRunPayload,
                        db: Session = Depends(get_session),
                        _su: CurrentUser = Depends(require_superadmin)) -> dict:
    """Enqueue a full re-ingest (re-parse → chunk → embed → extract) for the given
    doc pks, or for the whole candidate set of a symptom. Bounded by `limit`."""
    from app.services import reprocess
    try:
        return await reprocess.run(db, get_current_tenant(), symptom=payload.symptom,
                                   doc_type=payload.docType, doc_pks=payload.docPks,
                                   limit=payload.limit)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ── block_map one-shot ──────────────────────────────────────────────────────
# Populates the block_map JSONB column (migration 0107) for all existing docs
# that have source bytes.  Parse-only: no re-chunk / re-embed / re-extract.

@router.post("/reprocess/populate-block-maps")
def populate_block_maps(dryRun: bool = Query(False),
                        db: Session = Depends(get_session),
                        _su: CurrentUser = Depends(require_superadmin)) -> dict:
    from app.services import reprocess
    return reprocess.populate_block_maps(db, get_current_tenant(), dry_run=dryRun)
