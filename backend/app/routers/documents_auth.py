"""M46 · Documents System · self-registration.

Kept in its own documents-owned router (not in the shared `routers/auth.py`) so
the documents footprint on shared auth code is zero. Open registration is a
documents-product affordance only — the auditing product creates users via
admin invite (POST /api/users). A new account gets the `owner` role (it owns its
own private workspace) and is logged in immediately; per-user data isolation is
enforced downstream by the owner-scope middleware + repository filters.

Reuses the cookie helper + MeResponse from `routers/auth.py` (read-only import).
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from app.auth import hash_password, issue_session_token
from app.config import get_settings
from app.db import get_session, set_current_tenant
from app.repositories import users as user_repo
from app.routers.auth import MeResponse, _set_session_cookie
from app.security import CurrentUser, get_current_user

router = APIRouter()
log = logging.getLogger("docaiq.documents_auth")


class RegisterPayload(BaseModel):
    email: EmailStr
    password: str
    name: str | None = None
    # §compliance · explicit consent to processing + third-party LLM
    # sub-processors. The signup form must show the consent text + this box.
    consent: bool = False


@router.post("/auth/register", response_model=MeResponse, status_code=status.HTTP_201_CREATED)
def register(
    payload: RegisterPayload,
    request: Request,
    response: Response,
    db: Session = Depends(get_session),
) -> MeResponse:
    settings = get_settings()
    # M48 · abuse guard: cap sign-ups per client IP (behind nginx → use the
    # forwarded client address). 429s a flood of fake registrations.
    fwd = request.headers.get("x-forwarded-for", "")
    client_ip = (fwd.split(",")[0].strip() if fwd else None) or (request.client.host if request.client else "unknown")
    from app.rate_limit import rate_limit
    rate_limit(client_ip, action="register")
    if settings.product != "documents":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Self-registration is only available on the Documents product",
        )
    if settings.auth_provider != "dev":
        # Registration creates a password account; it only makes sense where
        # password login is enabled.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Password registration disabled in this environment",
        )
    if len(payload.password) < 8:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password must be at least 8 characters",
        )
    if not payload.consent:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You must consent to processing to create an account.",
        )

    # Establish the container's tenant for the unauthenticated create path.
    set_current_tenant(settings.tenant_id)
    if user_repo.get_by_email(db, str(payload.email)):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with that email already exists",
        )

    display_name = (payload.name or "").strip() or str(payload.email).split("@")[0]
    user = user_repo.create(
        db,
        email=str(payload.email),
        name=display_name,
        roles=["owner"],
        password_hash=hash_password(payload.password),
    )
    # M47 · start the 7-day trial (full Pro access) on signup.
    try:
        import datetime as _dt
        from app.services.subscriptions import TRIAL_DAYS
        now = _dt.datetime.now(_dt.timezone.utc)
        user.plan = "trial"
        user.trial_ends_at = now + _dt.timedelta(days=TRIAL_DAYS)
        user.plan_since = now
        db.flush()
    except Exception as e:  # noqa: BLE001 — never block signup
        log.warning("register: trial setup failed for %s: %s", payload.email, e)
    # §compliance · record the signup processing consent.
    try:
        from app.services import consent as consent_svc
        consent_svc.record(db, tenant_id=settings.tenant_id, user_id=user.pk,
                           kind=consent_svc.KIND_PROCESSING)
    except Exception as e:  # noqa: BLE001 — never block signup on this
        log.warning("register: consent record failed for %s: %s", payload.email, e)
    # M46 · §1 · link any pending group invites sent to this email before they
    # had an account, so they immediately see the groups they were added to.
    try:
        from app.routers.groups import link_pending_group_invites
        linked = link_pending_group_invites(db, user.pk, str(payload.email))
        if linked:
            log.info("register: linked %d pending group invite(s) for %s", linked, payload.email)
    except Exception as e:  # noqa: BLE001 — never block signup on this
        log.warning("register: pending-invite link failed for %s: %s", payload.email, e)
    # M48 · email verification. Only gate when a sender (Resend) is configured;
    # otherwise auto-verify so users aren't stuck behind a banner with no email
    # ever arriving. Activates automatically the moment DOCAIQ_RESEND_API_KEY is set.
    if settings.resend_api_key:
        try:
            from app.auth import issue_email_token
            from app.email import send_verification_email
            vtoken = issue_email_token(str(payload.email))
            verify_url = f"{settings.public_url.rstrip('/')}/api/auth/verify?token={vtoken}"
            send_verification_email(to=str(payload.email), name=display_name, verify_url=verify_url)
        except Exception as e:  # noqa: BLE001 — never block signup on email
            log.warning("register: verification email failed for %s: %s", payload.email, e)
    else:
        user.email_verified = True  # no sender configured → verification disabled
        db.flush()
    # No explicit commit — get_session commits on success (repo.create flushed,
    # so user.pk is already populated for the token below).

    roles = sorted(r.role for r in user.roles)
    token = issue_session_token(
        user_id=user.pk, email=user.email, name=user.name,
        org_id=settings.tenant_id, roles=roles,
        vendor_pk=None, token_version=getattr(user, "token_version", 0),
    )
    _set_session_cookie(response, token)
    return MeResponse(id=user.pk, email=user.email, name=user.name,
                      orgId=settings.tenant_id, roles=roles,
                      emailVerified=bool(getattr(user, "email_verified", True)))


@router.post("/auth/sso/exchange", response_model=MeResponse)
def sso_exchange(response: Response, jicama_sso: str | None = Cookie(default=None),
                 db: Session = Depends(get_session)) -> MeResponse:
    """AIQ Suite SSO · exchange the shared `jicama_sso` cookie for a native docaiq
    session — 'one login across the suite'. Find-or-create the user by the token's
    email, then mint the docaiq session cookie. 401 when there's no valid suite token
    (the SPA falls back to the normal login screen). See SSO_VERIFIER.md."""
    from app.sso import verify_sso
    settings = get_settings()
    claims = verify_sso(jicama_sso)
    if not claims:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="no valid suite session")
    email = (claims.get("sub") or "").strip().lower()
    if not email:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="suite token missing subject")
    set_current_tenant(settings.tenant_id)
    user = user_repo.get_by_email(db, email)
    if user is None:
        user = user_repo.create(db, email=email, name=claims.get("name", ""), roles=["owner"])
        user.email_verified = True  # a verified suite identity
        db.flush()
    roles = sorted(r.role for r in user.roles)
    token = issue_session_token(user_id=user.pk, email=user.email, name=user.name,
                                org_id=settings.tenant_id, roles=roles, vendor_pk=None,
                                token_version=getattr(user, "token_version", 0))
    _set_session_cookie(response, token)
    return MeResponse(id=user.pk, email=user.email, name=user.name, orgId=settings.tenant_id,
                      roles=roles, emailVerified=True)


@router.get("/auth/verify")
def verify_email(token: str, db: Session = Depends(get_session)):
    """Public link target from the verification email → mark the address
    verified, then bounce to the app with a status flag."""
    from fastapi.responses import RedirectResponse
    settings = get_settings()
    set_current_tenant(settings.tenant_id)
    try:
        from app.auth import verify_email_token
        email = verify_email_token(token)
    except Exception:  # noqa: BLE001 — expired/forged/used
        return RedirectResponse(url="/?verified=0", status_code=302)
    row = user_repo.get_by_email(db, email)
    if row is not None and not row.email_verified:
        row.email_verified = True
        db.commit()
    return RedirectResponse(url="/?verified=1", status_code=302)


@router.post("/auth/resend-verification")
def resend_verification(db: Session = Depends(get_session),
                        user: CurrentUser = Depends(get_current_user)) -> dict:
    """Re-send the verification email to the signed-in user (rate-limited)."""
    settings = get_settings()
    set_current_tenant(settings.tenant_id)
    row = user_repo.get_by_email(db, user.email)
    if row is None:
        raise HTTPException(status_code=404, detail="account not found")
    if row.email_verified:
        return {"status": "already_verified"}
    from app.rate_limit import rate_limit
    rate_limit(user.email, action="register")  # reuse the 5/hr cap to stop resend spam
    from app.auth import issue_email_token
    from app.email import send_verification_email
    vtoken = issue_email_token(user.email)
    verify_url = f"{settings.public_url.rstrip('/')}/api/auth/verify?token={vtoken}"
    send_verification_email(to=user.email, name=row.name, verify_url=verify_url)
    return {"status": "sent"}


# ── Public "Contact us" form ──────────────────────────────────────────────────
_FREE_MAIL_DOMAINS = {
    "gmail.com", "googlemail.com", "yahoo.com", "yahoo.co.uk", "outlook.com",
    "hotmail.com", "live.com", "msn.com", "icloud.com", "me.com", "mac.com",
    "aol.com", "proton.me", "protonmail.com", "pm.me", "gmx.com", "mail.com",
    "yandex.com", "zoho.com", "qq.com", "163.com",
}


class ContactPayload(BaseModel):
    firstName: str
    lastName: str
    businessEmail: EmailStr   # EmailStr validates the format; free-mail filtered below
    description: str


@router.post("/contact")
def contact_us(payload: ContactPayload) -> dict:
    """Public 'Contact us' form. Validates a real BUSINESS email (valid format +
    not a personal/free-mail domain), then emails the inquiry to contact_email."""
    import html as _html
    settings = get_settings()
    fn = (payload.firstName or "").strip()[:80]
    ln = (payload.lastName or "").strip()[:80]
    email = str(payload.businessEmail).strip().lower()
    desc = (payload.description or "").strip()[:5000]
    if not (fn and ln and desc):
        raise HTTPException(status_code=400, detail="First name, last name and description are all required.")
    if email.rsplit("@", 1)[-1] in _FREE_MAIL_DOMAINS:
        raise HTTPException(status_code=400,
                            detail="Please use your business email (not a personal Gmail/Yahoo/Outlook address).")
    to = settings.contact_email or settings.email_from
    e = _html.escape
    html_body = (
        "<p><b>New contact request — DocAIQ</b></p>"
        f"<p><b>Name:</b> {e(fn)} {e(ln)}<br><b>Business email:</b> {e(email)}</p>"
        f"<p><b>Message:</b><br>{e(desc).replace(chr(10), '<br>')}</p>"
    )
    text_body = f"New contact — {fn} {ln} <{email}>\n\n{desc}"
    try:
        from app.email import send_email
        send_email(to=to, subject=f"DocAIQ contact: {fn} {ln}", html=html_body, text=text_body)
    except Exception as ex:  # noqa: BLE001
        log.warning("contact_us: send failed: %s", ex)  # send_email logs in dev mode anyway
    return {"ok": True}
