"""M47 · per-user subscriptions for the Documents product.

Plans: trial → free → pro → enterprise.
  · A new user starts on a 7-day **trial** with full Pro access.
  · After the trial ends, the *effective* plan is **free** (capped) unless the
    user has been moved to **pro** / **enterprise** (paid).
  · **free** caps document count + monthly AI messages and locks the Pro-only
    features; PII protection stays on for everyone (a trust signal, cheap).

Metering targets AI usage + doc count — NOT storage, because originals live in
the user's own Drive (near-zero cost to us). Enterprise = a dedicated container
(the existing per-tenant model); this module covers the in-container trial/free/
pro plans.
"""
from __future__ import annotations

import datetime as _dt

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_current_tenant
from app.orm import ChatMessage, Document, PlanConfig, User

TRIAL_DAYS = 7

# Pro feature keys gated for free users.
# Pro-gated feature keys (each has an enforce_feature() gate at its call site).
# `search` is intentionally NOT here — basic semantic search stays free so trial/
# free users can explore + run a real POC. PII redaction is also free for everyone.
_PRO_FEATURES = {"groups", "encryption", "workspace", "export", "bulk"}

# Code defaults — the fallback when a plan has no DB override (PlanConfig row).
# `docs`/`ai_monthly` are now MONTHLY quotas (null = unlimited). Pro is metered at
# 30 docs/month per product decision; superadmin can change any of this live.
# The **trial** is the "start free" offer: a bounded POC/demo — 7 documents over the
# 7-day window (TRIAL_DAYS) WITH full Pro features. After it ends, the effective plan
# drops to **free** (also 7 docs, Pro features locked).
# `maxPages` (per DOCUMENT) exists so a free user can't game the 7-document cap by
# uploading one big PDF. Free is a lightweight TEST tier: 7 single-page documents.
# Paid/trial plans are uncapped on pages. Code-default only (no PlanConfig column) —
# superadmin tunes docs/ai/features live; the page cap is a product invariant.
DEFAULT_PLANS: dict[str, dict] = {
    "free":       {"enabled": True, "docs": 7,    "ai_monthly": 50,   "maxPages": 1,    "features": set(),             "paidModels": False, "llmEnabled": True,  "dedicatedContainer": False},
    "trial":      {"enabled": True, "docs": 7,    "ai_monthly": None, "maxPages": None, "features": set(_PRO_FEATURES), "paidModels": True,  "llmEnabled": True,  "dedicatedContainer": False},
    "pro":        {"enabled": True, "docs": 30,   "ai_monthly": None, "maxPages": None, "features": set(_PRO_FEATURES), "paidModels": True,  "llmEnabled": True,  "dedicatedContainer": False},
    "enterprise": {"enabled": True, "docs": None, "ai_monthly": None, "maxPages": None, "features": _PRO_FEATURES | {"sso", "roles", "dpa"}, "paidModels": True, "llmEnabled": True, "dedicatedContainer": True},
}
# Back-compat alias (older imports referenced PLANS).
PLANS = DEFAULT_PLANS

# Tiny in-process cache of merged config per tenant; cleared on any write.
_cfg_cache: dict[str, dict[str, dict]] = {}


def _clear_cfg_cache() -> None:
    _cfg_cache.clear()


def plan_configs(db: Session) -> dict[str, dict]:
    """Effective config for every plan = DB overrides merged over DEFAULT_PLANS,
    for the current tenant. Cached per tenant."""
    tid = get_current_tenant() or ""
    cached = _cfg_cache.get(tid)
    if cached is not None:
        return cached
    merged = {p: dict(cfg) for p, cfg in DEFAULT_PLANS.items()}
    try:
        rows = db.scalars(select(PlanConfig).where(PlanConfig.tenant_id == tid)).all()
        for r in rows:
            if r.plan not in merged:
                continue
            c = merged[r.plan]
            c["enabled"] = bool(r.enabled)
            c["docs"] = r.docs_monthly
            c["ai_monthly"] = r.ai_monthly
            if r.features is not None:
                c["features"] = set(r.features)
            c["paidModels"] = bool(r.paid_models)
            c["llmEnabled"] = bool(r.llm_enabled)
            c["dedicatedContainer"] = bool(r.dedicated_container)
    except Exception:  # noqa: BLE001 — never break enforcement on a config read
        pass
    _cfg_cache[tid] = merged
    return merged


def plan_cfg(db: Session, plan: str) -> dict:
    return plan_configs(db).get(plan, DEFAULT_PLANS["free"])


def _now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def _aware(dt: _dt.datetime | None) -> _dt.datetime | None:
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=_dt.timezone.utc)
    return dt


def _trial_end(user: User) -> _dt.datetime | None:
    """When this user's trial ends. Prefers the explicit column; for legacy
    users (pre-M47, trial_ends_at NULL) anchors the 7-day window on signup so
    they aren't left on a perpetual trial — no backfill migration needed."""
    if user.trial_ends_at is not None:
        return _aware(user.trial_ends_at)
    created = _aware(getattr(user, "created_at", None))
    return created + _dt.timedelta(days=TRIAL_DAYS) if created is not None else None


def effective_plan(user: User) -> str:
    """The plan currently in force — resolves an expired trial to 'free'."""
    plan = (user.plan or "trial")
    if plan == "trial":
        end = _trial_end(user)
        if end is not None and _now() > end:
            return "free"
        return "trial"
    # Promo-granted paid plans are time-limited — revert to free once past the expiry.
    exp = _aware(getattr(user, "plan_expires_at", None))
    if plan in ("pro", "enterprise") and exp is not None and _now() > exp:
        return "free"
    return plan if plan in PLANS else "free"


def trial_days_left(user: User) -> int | None:
    if (user.plan or "") != "trial":
        return None
    end = _trial_end(user)
    if end is None:
        return None
    import math
    secs = (end - _now()).total_seconds()
    return max(0, math.ceil(secs / 86400)) if secs > 0 else 0


def has_feature(db: Session, user: User, feature: str) -> bool:
    return feature in plan_cfg(db, effective_plan(user))["features"]


def doc_count(db: Session, *, tenant_id: str, owner_user_id: int) -> int:
    return int(db.scalar(select(func.count()).select_from(Document).where(
        Document.tenant_id == tenant_id, Document.owner_user_id == owner_user_id,
        Document.is_archived.is_(False))) or 0)


def docs_this_month(db: Session, *, tenant_id: str, owner_user_id: int) -> int:
    """The user's documents created THIS calendar month (the cap window)."""
    start = _now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return int(db.scalar(select(func.count()).select_from(Document).where(
        Document.tenant_id == tenant_id, Document.owner_user_id == owner_user_id,
        Document.is_archived.is_(False), Document.created_at >= start)) or 0)


def ai_messages_this_month(db: Session, *, tenant_id: str, owner_user_id: int) -> int:
    """Count the user's AI chat questions this calendar month (doc + workspace)."""
    start = _now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    doc_ids = select(Document.id_external).where(Document.owner_user_id == owner_user_id)
    return int(db.scalar(select(func.count()).select_from(ChatMessage).where(
        ChatMessage.tenant_id == tenant_id, ChatMessage.role == "user",
        ChatMessage.created_at >= start,
        (ChatMessage.doc_id_external.in_(doc_ids)) | (ChatMessage.workspace_key == f"user:{owner_user_id}"),
    )) or 0)


def _gate(code: str, message: str, **extra) -> HTTPException:
    return HTTPException(status_code=402,
                         detail={"code": code, "message": message, "upgrade": True, **extra})


def redeem_promo(db: Session, *, user: User, code: str) -> dict:
    """Validate + apply a promo code to `user`. Raises a 402 gate on invalid / expired /
    fully-redeemed. On success: sets plan + plan_since + plan_expires_at (now +
    duration_days), bumps the code's redemption count, and returns the grant."""
    from app.orm import PromoCode

    norm = (code or "").strip()
    if not norm:
        raise _gate("promo_invalid", "Enter a promo code.")
    row = db.scalar(select(PromoCode).where(
        PromoCode.tenant_id == user.tenant_id,
        func.upper(PromoCode.code) == norm.upper()))
    if row is None or not row.active:
        raise _gate("promo_invalid", "That promo code isn't valid.")
    now = _now()
    if row.expires_at is not None and now > _aware(row.expires_at):
        raise _gate("promo_expired", "That promo code has expired.")
    if row.max_redemptions is not None and row.redemptions >= row.max_redemptions:
        raise _gate("promo_used", "That promo code has already been fully redeemed.")

    user.plan = row.plan
    user.plan_since = now
    user.plan_expires_at = now + _dt.timedelta(days=row.duration_days)
    row.redemptions += 1
    db.commit()
    return {"plan": row.plan, "durationDays": row.duration_days,
            "expiresAt": user.plan_expires_at.isoformat()}


def enforce_upload(db: Session, *, tenant_id: str, owner_user_id: int) -> None:
    """402 when the user has hit their plan's document cap. The **free** plan is a
    HARD cap on ACTIVE documents (a true "7 documents" limit — archiving/deleting
    frees a slot, but they can never hold more than the cap at once, so it can't be
    churned monthly). Paid/trial plans keep the monthly-quota semantics. Enforced
    for every plan whose `docs` quota is non-null; enterprise (null) is unlimited."""
    user = db.get(User, owner_user_id)
    if user is None:
        return
    eff = effective_plan(user)
    cap = plan_cfg(db, eff)["docs"]
    if cap is None:
        return
    used = (doc_count(db, tenant_id=tenant_id, owner_user_id=owner_user_id) if eff == "free"
            else docs_this_month(db, tenant_id=tenant_id, owner_user_id=owner_user_id))
    if used >= cap:
        window = "" if eff == "free" else " this month"
        raise _gate("plan_limit", f"{eff.capitalize()} plan limit reached: {cap} documents{window}.",
                    cap=cap, used=used)


def enforce_pages(db: Session, *, owner_user_id: int, pages: int | None) -> None:
    """402 when a document exceeds the plan's per-document page cap (free = 7). Keeps
    a free user from slipping a 100-page PDF past the 7-document cap. No-op when the
    plan has no page cap (trial/pro/enterprise) or the page count is unknown."""
    if pages is None:
        return
    user = db.get(User, owner_user_id)
    if user is None:
        return
    eff = effective_plan(user)
    cap = plan_cfg(db, eff).get("maxPages")
    if cap is not None and pages > cap:
        limit_txt = "single-page documents only" if cap == 1 else f"up to {cap} pages per document"
        raise _gate(
            "plan_pages",
            f"The {eff} plan allows {limit_txt} — this document has {pages} pages. "
            f"Split it into single-page files, or upgrade for multi-page documents.",
            maxPages=cap, pages=pages,
        )


def enforce_chat(db: Session, *, tenant_id: str, owner_user_id: int) -> None:
    """402 when LLM is disabled for the plan, or the monthly AI-message cap is hit."""
    user = db.get(User, owner_user_id)
    if user is None:
        return
    eff = effective_plan(user)
    cfg = plan_cfg(db, eff)
    if not cfg.get("llmEnabled", True):
        raise _gate("plan_llm_disabled", f"AI chat isn't included in the {eff} plan.")
    cap = cfg["ai_monthly"]
    if cap is not None and ai_messages_this_month(db, tenant_id=tenant_id, owner_user_id=owner_user_id) >= cap:
        raise _gate("plan_limit",
                    f"You've used all {cap} AI messages included with the {eff.capitalize()} plan "
                    f"this month (resets on the 1st). Upgrade for unlimited chat.")


def page_cap_for(db: Session, *, owner_user_id: int) -> int | None:
    """The per-document page cap for this user's effective plan (free = 7), or None
    when uncapped. Non-raising — used by the ingestion worker (all paths) as the
    backstop to the upload-time `enforce_pages` check."""
    user = db.get(User, owner_user_id)
    if user is None:
        return None
    return plan_cfg(db, effective_plan(user)).get("maxPages")


def enforce_feature(db: Session, *, owner_user_id: int, feature: str) -> None:
    """402 when a free user tries a Pro-only feature."""
    user = db.get(User, owner_user_id)
    if user is not None and not has_feature(db, user, feature):
        raise _gate("plan_feature", "This is a Pro feature — upgrade to use it.", feature=feature)


def usage_summary(db: Session, user: User, *, tenant_id: str) -> dict:
    """For /me + the upgrade banner: plan, trial, caps, current usage."""
    eff = effective_plan(user)
    cfg = plan_cfg(db, eff)
    docs_total = doc_count(db, tenant_id=tenant_id, owner_user_id=user.pk)
    docs_mo = docs_this_month(db, tenant_id=tenant_id, owner_user_id=user.pk)
    ai = ai_messages_this_month(db, tenant_id=tenant_id, owner_user_id=user.pk)
    return {
        "plan": user.plan,
        "effectivePlan": eff,
        "trialEndsAt": user.trial_ends_at.isoformat() if user.trial_ends_at else None,
        "trialDaysLeft": trial_days_left(user),
        "paidModels": cfg["paidModels"],
        "llmEnabled": cfg.get("llmEnabled", True),
        "dedicatedContainer": cfg.get("dedicatedContainer", False),
        "features": sorted(cfg["features"]),
        "limits": {"docsMonthly": cfg["docs"], "aiMonthly": cfg["ai_monthly"], "maxPages": cfg.get("maxPages")},
        "usage": {"docs": docs_total, "docsThisMonth": docs_mo, "aiThisMonth": ai},
    }


# ---- Superadmin plan-config management ------------------------------------
def list_plan_configs(db: Session) -> list[dict]:
    """All plans with their effective (DB-merged) config, for the admin console."""
    cfgs = plan_configs(db)
    order = ["free", "pro", "enterprise", "trial"]
    return [{
        "plan": p,
        "enabled": cfgs[p].get("enabled", True),
        "docsMonthly": cfgs[p]["docs"],
        "aiMonthly": cfgs[p]["ai_monthly"],
        "paidModels": cfgs[p]["paidModels"],
        "llmEnabled": cfgs[p].get("llmEnabled", True),
        "dedicatedContainer": cfgs[p].get("dedicatedContainer", False),
        "features": sorted(cfgs[p]["features"]),
    } for p in order if p in cfgs]


def set_plan_config(db: Session, plan: str, *, tenant_id: str, **fields) -> dict:
    """Upsert a plan's config (superadmin). Only provided fields change."""
    if plan not in DEFAULT_PLANS:
        raise ValueError(f"unknown plan {plan!r}")
    row = db.get(PlanConfig, (tenant_id, plan))
    if row is None:
        d = DEFAULT_PLANS[plan]
        row = PlanConfig(
            tenant_id=tenant_id, plan=plan, enabled=d["enabled"],
            docs_monthly=d["docs"], ai_monthly=d["ai_monthly"],
            features=sorted(d["features"]), paid_models=d["paidModels"],
            llm_enabled=d["llmEnabled"], dedicated_container=d["dedicatedContainer"],
        )
        db.add(row)
    _M = {"enabled": "enabled", "docsMonthly": "docs_monthly", "aiMonthly": "ai_monthly",
          "paidModels": "paid_models", "llmEnabled": "llm_enabled",
          "dedicatedContainer": "dedicated_container", "features": "features"}
    # Update every PROVIDED key — None is a real value here (= unlimited for quotas).
    for k, v in fields.items():
        if k in _M:
            setattr(row, _M[k], sorted(v) if (k == "features" and v is not None) else v)
    row.updated_at = _now()
    db.commit()
    _clear_cfg_cache()
    return {p["plan"]: p for p in list_plan_configs(db)}[plan]
