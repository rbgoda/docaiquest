"""Auth endpoints.

Routes
------
* POST /api/auth/login         — dev-mode email+password → cookie + user
* POST /api/auth/logout        — clear cookie
* GET  /api/auth/google/login  — 302 to Google authz
* GET  /api/auth/google/callback — Google sends the code here
* GET  /api/me                 — the logged-in user (for the AuthContext bootstrap)
* GET  /api/auth/config        — public: which login flows are enabled
"""

from __future__ import annotations

import logging
import secrets
from urllib.parse import quote

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from app.auth import (
    exchange_google_code,
    google_authz_url,
    issue_session_token,
    password_ok,
    verify_google_id_token,
)
from app.config import get_settings
from app.db import get_session, set_current_tenant
from app.repositories import users as user_repo
from app.security import CurrentUser, get_current_user

router = APIRouter()
log = logging.getLogger("docaiq.auth")


def _provision_documents_user(db: Session, *, email: str, name: str | None, tenant_id: str):
    """M48 · auto-create a Documents account on first Google sign-in (public
    signup). Mirrors the email/password register: owner account (no password) +
    7-day trial + processing consent + pending group-invite linking. Documents
    product only — other products keep the admin-created allowlist."""
    display = (name or "").strip() or email.split("@")[0]
    user = user_repo.create(db, email=email, name=display, roles=["owner"], password_hash=None)
    # Google already verified this address — no email-verification step needed.
    user.email_verified = True
    try:
        import datetime as _dt
        from app.services.subscriptions import TRIAL_DAYS
        now = _dt.datetime.now(_dt.timezone.utc)
        user.plan = "trial"
        user.trial_ends_at = now + _dt.timedelta(days=TRIAL_DAYS)
        user.plan_since = now
        db.flush()
    except Exception as e:  # noqa: BLE001 — never block signup
        log.warning("google provision: trial setup failed for %s: %s", email, e)
    try:
        from app.services import consent as consent_svc
        consent_svc.record(db, tenant_id=tenant_id, user_id=user.pk, kind=consent_svc.KIND_PROCESSING)
    except Exception as e:  # noqa: BLE001
        log.warning("google provision: consent record failed for %s: %s", email, e)
    try:
        from app.routers.groups import link_pending_group_invites
        link_pending_group_invites(db, user.pk, email)
    except Exception as e:  # noqa: BLE001
        log.warning("google provision: pending-invite link failed for %s: %s", email, e)
    log.info("google provision: created Documents account for %s", email)
    return user


# ---- Request/response models -----------------------------------------------
class LoginPayload(BaseModel):
    email: EmailStr
    password: str


class DevSeedAccount(BaseModel):
    email: str
    name: str | None = None
    roles: list[str]


class AuthConfigResponse(BaseModel):
    devLoginEnabled: bool
    googleLoginEnabled: bool
    tenant: str
    # M37 · true on the shared free-tier SaaS container. The login UI uses this
    # to hide the per-tenant chrome (the "__shared__" tenant label) that only
    # makes sense for a dedicated per-customer container.
    sharedMode: bool = False
    # M46 · "auditing" | "documents" — which product this stack serves, so the
    # frontend renders the right shell (full audit app vs documents-only).
    product: str = "documents"
    # P2 · deployment license mode. "oss" = self-hosted open-source (default):
    # cloud-only premium features are disabled and the UI hides their chrome.
    # "cloud" = managed cloud build with full feature set.
    licenseMode: str = "oss"
    # Dev mode only: enumerate the password-login accounts that exist so the
    # login page can offer click-to-prefill chips. Empty when dev mode is off
    # or when the tenant DB has no password users (Google-only deployments).
    devAccounts: list[DevSeedAccount] = []


class MeResponse(BaseModel):
    id: int
    email: str
    name: str
    orgId: str
    roles: list[str]
    # M46 · whether this account has a password (vs. Google-only). Optional so
    # existing construction sites that don't set it stay valid; the /me
    # bootstrap populates it so the profile panel can show "Signed in with".
    hasPassword: bool | None = None
    # M47 · per-user subscription summary (documents product). None elsewhere.
    subscription: dict | None = None
    # M47 · whether this account may use the superadmin console.
    isSuperadmin: bool = False
    # M48 · email verification state (Documents). True for Google + grandfathered
    # accounts; False for unverified email/password signups (drives the banner).
    emailVerified: bool = True


# ---- Cookie helpers --------------------------------------------------------
def _cookie_domain_kw() -> dict:
    """When session_cookie_domain is set (e.g. ".example.com") the session is shared across
    subdomains — a login on the main app authenticates the admin console too."""
    d = get_settings().session_cookie_domain.strip()
    return {"domain": d} if d else {}


def _safe_next(nxt: str | None, default: str = "/") -> str:
    """Validate an OAuth `next=` target to prevent open redirects: relative paths, or an absolute URL
    whose host is the public app OR shares the configured cookie domain."""
    if not nxt:
        return default
    if nxt.startswith("/") and not nxt.startswith("//"):
        return nxt
    try:
        from urllib.parse import urlparse
        p = urlparse(nxt)
    except Exception:  # noqa: BLE001
        return default
    if p.scheme not in ("http", "https") or not p.netloc:
        return default
    s = get_settings()
    allowed = {urlparse(s.public_url).netloc}
    dom = s.session_cookie_domain.strip().lstrip(".")
    host = p.netloc.split(":")[0]
    if host in allowed or (dom and (host == dom or host.endswith("." + dom))):
        return nxt
    return default


def _set_session_cookie(response: Response, token: str) -> None:
    settings = get_settings()
    is_prod = settings.environment == "production"
    response.set_cookie(
        key=settings.session_cookie_name,
        value=token,
        max_age=settings.jwt_ttl_seconds,
        httponly=True,
        samesite="lax",
        secure=is_prod,
        path="/",
        **_cookie_domain_kw(),
    )


def _clear_session_cookie(response: Response) -> None:
    """Delete the session cookie. Must mirror EVERY attribute that
    `_set_session_cookie` set — Safari and Firefox (with cookie
    partitioning enabled) will leave the original cookie alive if the
    deletion cookie's attributes don't match exactly."""
    settings = get_settings()
    is_prod = settings.environment == "production"
    response.delete_cookie(
        key=settings.session_cookie_name,
        path="/",
        httponly=True,
        samesite="lax",
        secure=is_prod,
        **_cookie_domain_kw(),
    )


# ---- Public: which flows are enabled? --------------------------------------
@router.get("/auth/config", response_model=AuthConfigResponse)
def auth_config(db: Session = Depends(get_session)) -> AuthConfigResponse:
    settings = get_settings()
    dev_enabled = settings.auth_provider == "dev"

    accounts: list[DevSeedAccount] = []
    # User-enumeration guard: NEVER expose the password-account list in
    # production. This endpoint is unauthenticated by design (so the login
    # page can fetch it before login). In dev/staging the click-to-prefill
    # chips speed up testing; in prod they hand attackers a valid email
    # list keyed to a public tenant slug.
    # M37 · never enumerate accounts on the shared free container. It hosts
    # many tenants behind one public host, so the click-to-prefill list would
    # leak one tenant's user emails to anyone hitting the login page — even
    # though the container runs with environment=development.
    # M49 · the documents product is a public self-serve app — never enumerate
    # registered emails on the unauthenticated /config endpoint, regardless of env.
    if (dev_enabled and settings.environment != "production"
            and not settings.shared_mode and settings.product != "documents"):
        # Enumerate password-login accounts so the login page can offer
        # click-to-prefill chips. Filtering by env tenant (TenantMiddleware
        # sets it pre-route) so the unauthenticated /config endpoint
        # returns the right tenant's seed users.
        from sqlalchemy import select as sa_select
        from sqlalchemy.orm import joinedload
        from app.orm import User
        tid = settings.tenant_id
        users = db.scalars(
            sa_select(User)
            .options(joinedload(User.roles))
            .where(User.tenant_id == tid, User.password_hash.is_not(None))
            .order_by(User.pk)
        ).unique().all()
        for u in users:
            roles = sorted({r.role for r in u.roles})
            accounts.append(DevSeedAccount(
                email=u.email,
                name=u.name,
                roles=roles,
            ))

    return AuthConfigResponse(
        devLoginEnabled=dev_enabled,
        googleLoginEnabled=bool(settings.google_client_id and settings.google_client_secret),
        tenant=settings.tenant_id,
        sharedMode=settings.shared_mode,
        product=settings.product,
        licenseMode=settings.license_mode,
        devAccounts=accounts,
    )


# ---- Dev-mode email + password ---------------------------------------------
@router.post("/auth/login", response_model=MeResponse)
def login_with_password(
    payload: LoginPayload,
    response: Response,
    request: Request,
    db: Session = Depends(get_session),
) -> MeResponse:
    settings = get_settings()
    if settings.auth_provider != "dev":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Password login disabled in this environment",
        )

    # Throttle online password guessing per client IP (the email is unverified
    # at this point, so we key on IP like the register guard). Fails open on a
    # Redis blip — see rate_limit.py.
    fwd = request.headers.get("x-forwarded-for", "")
    client_ip = (fwd.split(",")[0].strip() if fwd else None) or (
        request.client.host if request.client else "unknown")
    from app.rate_limit import rate_limit
    rate_limit(client_ip, action="login")

    # In shared_mode (M37 free SaaS) one container holds many tenants — look
    # up by email across the whole DB, then issue JWT bound to THAT user's
    # tenant_id. In normal per-tenant mode the lookup is scoped to the
    # container's env tenant via the ContextVar set by TenantMiddleware.
    if settings.shared_mode:
        # Bypass the repo's tenant filter · raw SQL via the User ORM.
        from app.orm import User as UserORM
        from sqlalchemy import select as _select
        user = db.scalar(_select(UserORM).where(UserORM.email == payload.email))
        # password_ok runs a full argon2 verify even on a miss (constant-time) — call
        # it BEFORE the `user is None` short-circuit so timing doesn't leak enumeration.
        pw_ok = password_ok(payload.password, user.password_hash if user else None)
        if user is None or not pw_ok:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid email or password",
            )
        org_id = user.tenant_id
        # Set context so mark_login + role-join run against the right tenant.
        set_current_tenant(org_id)
        # M42 · access-request gate. Frozen users can authenticate against
        # the password DB but cannot create a session — superadmin must
        # re-approve their access request first.
        if user.is_frozen:
            raise HTTPException(
                status_code=status.HTTP_423_LOCKED,
                detail={
                    "frozen": True,
                    "message": "Your workspace is under review. Submit an access request to restore access.",
                    "requestUrl": "/signup.html",
                },
            )
        user_repo.mark_login(db, user)
    else:
        # `current_tenant` is set by TenantMiddleware from env in
        # unauthenticated paths. Login establishes a session for THIS
        # container's tenant.
        set_current_tenant(settings.tenant_id)
        user = user_repo.get_by_email(db, payload.email)
        pw_ok = password_ok(payload.password, user.password_hash if user else None)
        if user is None or not pw_ok:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid email or password",
            )
        # M42 · access-request gate for password-based login on paid tenants.
        if user.is_frozen:
            raise HTTPException(
                status_code=status.HTTP_423_LOCKED,
                detail={
                    "frozen": True,
                    "message": "Your workspace is under review. Contact your administrator.",
                    "requestUrl": None,
                },
            )
        user_repo.mark_login(db, user)
        org_id = settings.tenant_id

    # M48 · optional hard gate: block unverified email/password logins when
    # DOCAIQ_EMAIL_VERIFICATION_REQUIRED is on. Default OFF (we use a soft
    # banner instead, to avoid lockouts if delivery hiccups during launch).
    if (settings.email_verification_required and settings.product == "documents"
            and not getattr(user, "email_verified", True)):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "email_unverified",
                    "message": "Please verify your email — check your inbox for the confirmation link."},
        )

    roles = sorted(r.role for r in user.roles)
    token = issue_session_token(
        user_id=user.pk, email=user.email, name=user.name,
        org_id=org_id, roles=roles,
        token_version=getattr(user, "token_version", 0),
    )
    _set_session_cookie(response, token)
    return MeResponse(id=user.pk, email=user.email, name=user.name,
                      orgId=org_id, roles=roles,
                      emailVerified=bool(getattr(user, "email_verified", True)))


# ---- Google OIDC -----------------------------------------------------------
def _google_redirect_uri() -> str:
    return f"{get_settings().public_url.rstrip('/')}/api/auth/google/callback"


@router.get("/auth/google/login")
def google_login(request: Request, next: str | None = None) -> RedirectResponse:
    settings = get_settings()
    if not settings.google_client_id:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Google login not configured for this tenant",
        )
    state = secrets.token_urlsafe(24)
    response = RedirectResponse(
        url=google_authz_url(_google_redirect_uri(), state),
        status_code=status.HTTP_302_FOUND,
    )
    is_prod = settings.environment == "production"
    # Persist `state` in a short-lived cookie so the callback can verify it
    # belongs to this browser session (CSRF defense for the OAuth flow).
    response.set_cookie(
        key="docaiq_oauth_state",
        value=state,
        max_age=600,
        httponly=True,
        samesite="lax",
        secure=is_prod,
        path="/api/auth/google/callback",
    )
    # Remember where to land after login (e.g. back on the admin console). Validated on the way out.
    response.set_cookie(
        key="docaiq_oauth_next",
        value=_safe_next(next),
        max_age=600,
        httponly=True,
        samesite="lax",
        secure=is_prod,
        path="/api/auth/google/callback",
    )
    return response


@router.get("/auth/google/callback")
def google_callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    request: Request = None,
    db: Session = Depends(get_session),
) -> RedirectResponse:
    settings = get_settings()
    if error:
        return RedirectResponse(
            url=f"/login?error={quote(error)}",
            status_code=status.HTTP_302_FOUND,
        )
    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing code or state")

    expected_state = request.cookies.get("docaiq_oauth_state")
    if not expected_state or not secrets.compare_digest(expected_state, state):
        raise HTTPException(status_code=400, detail="State mismatch (possible CSRF)")

    # Exchange the code, then validate Google's signed ID token. PyJWT verifies
    # the RS256 signature against Google's JWKS + checks aud/iss/exp/email_verified.
    try:
        id_token = exchange_google_code(code, _google_redirect_uri())
        claims = verify_google_id_token(id_token)
    except (jwt.PyJWTError, RuntimeError) as e:
        return RedirectResponse(
            url=f"/login?error={quote(str(e))}",
            status_code=status.HTTP_302_FOUND,
        )

    email = claims["email"].lower()
    # Allowlist enforced here — an unknown Google identity can authenticate
    # against Google but cannot create a session unless it maps to a user.
    if settings.shared_mode:
        # Shared free SaaS container: many tenants in one DB. Find the user by
        # email across all tenants, then bind the session to THAT user's
        # tenant_id (mirrors the password-login shared-mode path). Using
        # settings.tenant_id (=__shared__) here would never match.
        from app.orm import User as UserORM
        from sqlalchemy import select as _select
        user = db.scalar(_select(UserORM).where(UserORM.email == email))
        if user is None:
            return RedirectResponse(
                url="/login?error=access_denied",
                status_code=status.HTTP_302_FOUND,
            )
        org_id = user.tenant_id
        set_current_tenant(org_id)
    else:
        set_current_tenant(settings.tenant_id)
        user = user_repo.get_by_email(db, email)
        if user is None:
            # M48 · Documents is a public self-serve product: auto-provision the
            # account on first Google sign-in. Other products keep the
            # admin-created allowlist (no silent account creation).
            if settings.product == "documents":
                user = _provision_documents_user(
                    db, email=email, name=claims.get("name"), tenant_id=settings.tenant_id)
            else:
                return RedirectResponse(
                    url="/login?error=access_denied",
                    status_code=status.HTTP_302_FOUND,
                )
        org_id = settings.tenant_id

    # M42 · access-request gate. Frozen users get redirected to the
    # access-request form with a clear error message — no session token
    # is issued. Applies to both shared free + paid Google flows.
    if user.is_frozen:
        return RedirectResponse(
            url="/login?error=workspace_under_review",
            status_code=status.HTTP_302_FOUND,
        )

    user_repo.mark_login(db, user)
    roles = sorted(r.role for r in user.roles)
    token = issue_session_token(
        user_id=user.pk, email=user.email,
        name=user.name or claims.get("name") or user.email,
        org_id=org_id, roles=roles,
        token_version=getattr(user, "token_version", 0),
    )
    dest = _safe_next(request.cookies.get("docaiq_oauth_next")) if request else "/"
    response = RedirectResponse(url=dest, status_code=status.HTTP_302_FOUND)
    _set_session_cookie(response, token)
    response.delete_cookie("docaiq_oauth_state", path="/api/auth/google/callback")
    response.delete_cookie("docaiq_oauth_next", path="/api/auth/google/callback")
    return response


# ---- Logout ----------------------------------------------------------------
@router.post("/auth/logout")
def logout(response: Response) -> dict[str, str]:
    _clear_session_cookie(response)
    return {"status": "ok"}


@router.post("/auth/logout-all")
def logout_all(
    response: Response,
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, str]:
    """Log out of ALL devices: bump the user's token_version so every existing
    session JWT (this one included) is invalidated, then clear the local cookie.
    Takes effect immediately when session_revocation is on; the counter is bumped
    regardless so it's already correct if the flag is enabled later."""
    from sqlalchemy import select as _select

    from app.orm import User
    row = db.scalar(_select(User).where(User.pk == user.id))
    if row is not None:
        row.token_version = (row.token_version or 0) + 1
        db.flush()
    _clear_session_cookie(response)
    return {"status": "ok"}


# ---- Current user (AuthContext bootstrap) ----------------------------------
@router.get("/me", response_model=MeResponse)
def me(
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> MeResponse:
    """Return the logged-in user. Roles are RE-READ from the DB on every call
    so admin-side role changes (grant/revoke, demo-tier role backfills) reflect
    on the SPA bootstrap without forcing the user to log out and back in. The
    JWT's `roles` claim is still used by per-request route guards (faster);
    /api/me is the bootstrap surface and should show fresh DB state."""
    from app.orm import User as UserORM
    fresh_roles = list(user.roles)
    db_user = db.get(UserORM, user.id)
    has_password = None
    # Name is also re-read from the DB (not just roles) so a self-service
    # profile edit (PATCH /me) survives a page reload — the JWT still carries
    # the name baked in at login, which would otherwise show stale until the
    # next sign-in.
    fresh_name = user.name
    if db_user is not None:
        fresh_roles = sorted({r.role for r in db_user.roles})
        has_password = bool(db_user.password_hash)
        fresh_name = db_user.name or user.name
    # M47 · per-user subscription summary (documents product only).
    subscription = None
    if db_user is not None and get_settings().product == "documents":
        try:
            from app.services import subscriptions as subs
            subscription = subs.usage_summary(db, db_user, tenant_id=user.org_id)
        except Exception:  # noqa: BLE001
            subscription = None
    _s = get_settings()
    # Superadmin = env bootstrap list OR the console-managed DB allowlist
    # (SuperadminAllow). MUST mirror require_superadmin's check — otherwise a
    # console-added admin passes the /superadmin API gate but this bootstrap
    # reports isSuperadmin=false, so the SPA bounces them to "not authorized"
    # (they then think Google login is broken and fall back to a password).
    is_superadmin = False
    if _s.product == "documents" and db_user is not None:
        _admin_email = (db_user.email or "").lower()
        if _admin_email in _s.superadmin_email_set:
            is_superadmin = True
        else:
            from app.orm import SuperadminAllow
            from sqlalchemy import select as _sa_select
            is_superadmin = db.scalar(_sa_select(SuperadminAllow).where(
                SuperadminAllow.tenant_id == db_user.tenant_id,
                SuperadminAllow.email == _admin_email,
            )) is not None
    email_verified = bool(getattr(db_user, "email_verified", True)) if db_user is not None else True
    return MeResponse(
        id=user.id, email=user.email, name=fresh_name,
        orgId=user.org_id, roles=fresh_roles, hasPassword=has_password,
        subscription=subscription, isSuperadmin=is_superadmin,
        emailVerified=email_verified,
    )


class UpdateProfilePayload(BaseModel):
    """Fields a user is allowed to change about themselves. NEVER includes
    email (would break login) or roles (would be a privilege escalation
    vector). Admins use POST /api/users/{id}/roles for the role path."""
    name: str | None = None


@router.patch("/me", response_model=MeResponse)
def update_me(
    payload: UpdateProfilePayload,
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(get_current_user),
) -> MeResponse:
    """Self-service profile update (TODO #32). Previously, Settings →
    Profile only wrote to localStorage — confusing UX for a multi-tenant
    SaaS where the name shows up in audit logs and chat headers.
    Restricted to display fields only."""
    from app.orm import User
    from sqlalchemy import select as _select
    row = db.scalar(_select(User).where(User.pk == user.id))
    if row is None:
        raise HTTPException(status_code=404, detail="user not found")
    if payload.name is not None:
        name = payload.name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="name cannot be empty")
        if len(name) > 200:
            raise HTTPException(status_code=400, detail="name too long (max 200 chars)")
        row.name = name
    db.flush()
    return MeResponse(
        id=row.pk, email=row.email, name=row.name,
        orgId=user.org_id, roles=list(user.roles),
    )


class ChangePasswordPayload(BaseModel):
    """Self-service password change. Requires the current password to
    prevent CSRF / stolen-cookie hijack from being able to change it."""
    currentPassword: str
    newPassword: str


@router.patch("/me/password", status_code=204)
def change_my_password(
    payload: ChangePasswordPayload,
    response: Response,
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(get_current_user),
) -> None:
    """Self-service password change · verifies current via argon2 before
    hashing the new one. Min length 8 (matches the bootstrap-owner
    requirement). Google-only users (password_hash IS NULL) get a 409
    explaining they sign in via Google and have no password to change.
    """
    from app.auth import hash_password, verify_password
    from app.orm import User
    from sqlalchemy import select as _select

    row = db.scalar(_select(User).where(User.pk == user.id))
    if row is None:
        raise HTTPException(status_code=404, detail="user not found")
    if not row.password_hash:
        raise HTTPException(
            status_code=409,
            detail="This account signs in via Google · no password to change.",
        )

    if not verify_password(payload.currentPassword, row.password_hash):
        raise HTTPException(status_code=403, detail="Current password is incorrect")

    new_pw = (payload.newPassword or "").strip()
    if len(new_pw) < 8:
        raise HTTPException(
            status_code=400,
            detail="New password must be at least 8 characters",
        )
    if len(new_pw) > 256:
        raise HTTPException(status_code=400, detail="Password too long (max 256 chars)")
    if new_pw == payload.currentPassword:
        raise HTTPException(
            status_code=400,
            detail="New password must differ from the current one",
        )

    row.password_hash = hash_password(new_pw)
    # Session revocation: bump so every OTHER live session (e.g. a stolen cookie) is
    # invalidated by the password change, then re-issue THIS session's cookie at the
    # new version so the user who just changed their password stays signed in.
    row.token_version = (row.token_version or 0) + 1
    db.flush()
    fresh = issue_session_token(
        user_id=row.pk, email=row.email, name=row.name or row.email,
        org_id=user.org_id, roles=list(user.roles),
        token_version=row.token_version)
    _set_session_cookie(response, fresh)
