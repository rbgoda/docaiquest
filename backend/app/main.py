from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import os as _os

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

from app.config import get_settings
from app.db import SessionLocal, set_current_tenant
from app.middleware import RequestIdFilter, RequestIdMiddleware, TenantMiddleware
from app.routers import (
    alerts as alerts_router,
    analytics as analytics_router,
    auth as auth_router,
    categories as categories_router,
    connectors as connectors_router,
    dashboard as dashboard_router,
    doc_chat,
    doc_chat_export as doc_chat_export_router,
    documents,
    documents_fields as documents_fields_router,
    documents_review as documents_review_router,
    documents_upload as documents_upload_router,
    documents_auth as documents_auth_router,
    documents_dashboard as documents_dashboard_router,
    documents_analytics as documents_analytics_router,
    documents_feedback as documents_feedback_router,
    feedback as feedback_router,
    fleet as fleet_router,
    intelligence as intelligence_router,
    assistant as assistant_router,
    extraction as extraction_router,
    api_v1 as api_v1_router,
    keys as keys_router,
    mcp as mcp_router,
    groups as groups_router,
    data_rights as data_rights_router,
    graph as graph_router,
    learning as learning_router,
    llm_calls,
    llm_settings as llm_settings_router,
    retrieve as retrieve_router,
    superadmin as superadmin_router,
    usage as usage_router,
    users,
    workspace_chat as workspace_chat_router,
)
from app.security import get_current_user
from app.seed import seed_tenant

settings = get_settings()
# Structured log format with request_id pivot. The RequestIdFilter injects
# the field on every record; "-" appears when no request is in context
# (startup, worker job).
logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s [%(request_id)s] %(name)s · %(message)s",
)
for _h in logging.getLogger().handlers:
    _h.addFilter(RequestIdFilter())
# §A7 · httpx/httpcore log request URLs at INFO — and Google's Gemini endpoint
# carries the API key in the URL query (?key=...). Silence them to WARNING so a
# secret never lands in our logs.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
log = logging.getLogger("docaiq")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """On startup: ensure the current tenant is seeded. Alembic migrations run
    in the container entrypoint before this code is reached.
    """
    # M44.P5 · validate the chat pipeline ordering BEFORE we accept
    # traffic. Catches "added new LLM step above a free DB step"
    # misordering at boot, not after a customer's bill grows. Crashes
    # the container hard · uvicorn logs the AssertionError and the
    # healthcheck never goes green.
    from app.services.chat_pipeline import validate_pipeline
    validate_pipeline()

    # Fail fast on an embedding model whose native width ≠ DOCAIQ_EMBED_DIM —
    # otherwise vectors are silently coerced and corrupt cosine retrieval.
    from app.embeddings import assert_embed_dim
    # M47 · auto-create pg_trgm extension + trigram index for multilingual BM25
    from app.services.migration import ensure_extensions
    _boot_db = SessionLocal()
    try:
        ensure_extensions(_boot_db)
    finally:
        _boot_db.close()
    assert_embed_dim()

    set_current_tenant(settings.tenant_id)
    try:
        with SessionLocal() as session:
            seed_tenant(session)
            # Apply any superadmin LLM provider key/enable overrides onto settings.
            try:
                from app import llm_admin
                llm_admin.apply_overrides(session)
                # Seed the gateway's custom-provider cache so custom
                # providers route immediately after boot.
                llm_admin._refresh_gateway_cache(session)
            except Exception:  # noqa: BLE001
                log.warning("llm_admin: apply_overrides at boot failed (non-fatal)")
            # Warm the PII config cache so the gateway uses DB-stored
            # settings from the first LLM call.
            try:
                from app.routers.superadmin import _refresh_pii_config_cache
                _refresh_pii_config_cache(session)
            except Exception:  # noqa: BLE001
                log.warning("pii: config cache warm at boot failed (non-fatal)")
            # Warm the embedding config cache so the embedding module uses
            # DB-stored backend selection from the first embed() call.
            try:
                from app.embeddings import _refresh_embedding_config_cache
                _refresh_embedding_config_cache(session)
            except Exception:  # noqa: BLE001
                log.warning("embeddings: config cache warm at boot failed (non-fatal)")
            # Warm the feature-flags cache so all feature toggles use
            # DB-stored values from the first call.
            try:
                from app.feature_flags import _refresh_feature_flags_cache
                _refresh_feature_flags_cache(session)
            except Exception:  # noqa: BLE001
                log.warning("feature_flags: config cache warm at boot failed (non-fatal)")
    except Exception:
        log.exception("Seed failed for tenant %s", settings.tenant_id)
        raise
    finally:
        set_current_tenant(None)
    # Fleet MEMBER: if this is a dedicated container pointed at a central
    # registry, register + heartbeat in the background (best-effort).
    if settings.fleet_admin_url and settings.instance_id and settings.fleet_token:
        import asyncio
        from app.routers.fleet import member_loop
        asyncio.create_task(member_loop())
        log.info("fleet: member heartbeat started → %s", settings.fleet_admin_url)
    yield


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description=(
        "**DocAIQuest API** — open-source document intelligence.\n\n"
        "Bring your own LLM keys, upload documents, and chat with them.\n\n"
        "- `POST /api/v1/ask` — grounded answer over *your* documents, with citations\n"
        "- `GET /api/v1/documents` — list your documents\n"
        "- `POST /api/extraction/extract` — structured fields from a file (stateless)\n"
        "- `POST /api/mcp` — Model Context Protocol endpoint for AI assistants (Claude, ChatGPT, agents)"
    ),
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# Middleware execute INSIDE-OUT (LIFO): request_id must wrap tenant so the
# tenant-resolution logs already carry the request id.
app.add_middleware(TenantMiddleware)
app.add_middleware(RequestIdMiddleware)


# ---- Public routes ---------------------------------------------------------
@app.get("/api/health", tags=["meta"])
def health() -> dict[str, str]:
    return {
        "status": "ok",
        "tenant": settings.tenant_id,
        "environment": settings.environment,
        "license_mode": settings.license_mode,
    }


# Auth router is public (its job *is* to issue sessions).
app.include_router(auth_router.router, prefix="/api", tags=["auth"])


# ---- Authenticated routes --------------------------------------------------
# Every domain router requires a valid session via the router-level dependency.
# Individual routes can layer `require_role(...)` on top for finer gating.
auth_dep = [Depends(get_current_user)]

# Documents product — only the routers this product serves.
app.include_router(documents.router, prefix="/api/documents", tags=["documents"], dependencies=auth_dep)
app.include_router(documents_fields_router.router, prefix="/api/documents", tags=["documents"], dependencies=auth_dep)
app.include_router(documents_review_router.router, prefix="/api/documents", tags=["documents"], dependencies=auth_dep)
app.include_router(documents_upload_router.router, prefix="/api/documents", tags=["documents"], dependencies=auth_dep)
app.include_router(categories_router.router, prefix="/api/categories", tags=["categories"], dependencies=auth_dep)
app.include_router(users.router, prefix="/api/users", tags=["users"], dependencies=auth_dep)
app.include_router(retrieve_router.router, prefix="/api", tags=["retrieve"], dependencies=auth_dep)
app.include_router(llm_calls.router, prefix="/api", tags=["llm"], dependencies=auth_dep)
app.include_router(analytics_router.router, prefix="/api", tags=["analytics"], dependencies=auth_dep)
app.include_router(learning_router.router, prefix="/api/learning", tags=["learning"], dependencies=auth_dep)
app.include_router(doc_chat.router, prefix="/api", tags=["doc-chat"], dependencies=auth_dep)
app.include_router(doc_chat_export_router.router, prefix="/api", tags=["doc-chat"], dependencies=auth_dep)
app.include_router(workspace_chat_router.router, prefix="/api", tags=["workspace-chat"], dependencies=auth_dep)
app.include_router(connectors_router.router, prefix="/api", tags=["connectors"], dependencies=auth_dep)
app.include_router(documents_dashboard_router.router, prefix="/api", tags=["documents-dashboard"], dependencies=auth_dep)
app.include_router(documents_analytics_router.router, prefix="/api", tags=["documents-analytics"], dependencies=auth_dep)
app.include_router(intelligence_router.router, prefix="/api", tags=["intelligence"], dependencies=auth_dep)
app.include_router(assistant_router.router, prefix="/api", tags=["assistant"], dependencies=auth_dep)
app.include_router(alerts_router.router, prefix="/api", tags=["alerts"], dependencies=auth_dep)
app.include_router(dashboard_router.router, prefix="/api", tags=["dashboard"], dependencies=auth_dep)
app.include_router(documents_feedback_router.router, prefix="/api", tags=["documents-feedback"], dependencies=auth_dep)
app.include_router(feedback_router.router, prefix="/api", tags=["product-feedback"], dependencies=auth_dep)
# Fleet sync — PUBLIC (token-gated, not session) so dedicated containers can register.
app.include_router(fleet_router.router, prefix="/api", tags=["fleet"])
app.include_router(groups_router.router, prefix="/api", tags=["documents-groups"], dependencies=auth_dep)
app.include_router(data_rights_router.router, prefix="/api", tags=["documents-data-rights"], dependencies=auth_dep)
app.include_router(graph_router.router, prefix="/api/graph", tags=["graph"], dependencies=auth_dep)
app.include_router(superadmin_router.router, prefix="/api/superadmin", tags=["superadmin"], dependencies=auth_dep)
# M46 · Documents self-registration is public (no auth_dep — you can't be logged in yet).
app.include_router(documents_auth_router.router, prefix="/api", tags=["documents-auth"])
# M49 · cross-app extraction API — service-key auth (X-API-Key), NOT session auth.
app.include_router(extraction_router.router, prefix="/api/extraction", tags=["extraction"])
# v1 partner API — own (per-partner key) auth, not the cookie session.
app.include_router(api_v1_router.router, prefix="/api/v1", tags=["api-v1"])
app.include_router(llm_settings_router.router, prefix="/api", tags=["llm-settings"], dependencies=auth_dep)
app.include_router(keys_router.router, prefix="/api", tags=["api-keys"], dependencies=auth_dep)
app.include_router(mcp_router.router, prefix="/api/mcp", tags=["mcp"])

app.include_router(usage_router.router, prefix="/api", tags=["usage"], dependencies=auth_dep)

# ── Dev: serve admin console locally (prod serves it via nginx) ──────────
_ADMIN_HTML = "/app/admin-ui-index.html"
if _os.path.isfile(_ADMIN_HTML):
    @app.get("/admin", response_class=HTMLResponse, include_in_schema=False)
    @app.get("/admin/", response_class=HTMLResponse, include_in_schema=False)
    @app.get("/admin/{rest:path}", response_class=HTMLResponse, include_in_schema=False)
    async def serve_admin_ui(rest: str = ""):
        """Serve admin console UI (dev only — nginx on prod)."""
        # Re-read on every request so you can edit index.html without restarting
        return HTMLResponse(open(_ADMIN_HTML, encoding="utf-8").read())

    # The admin UI is served at /admin as a raw HTML page. The JS inside
    # calls /api/superadmin/* (relative paths) which are still auth-gated
    # by the superadmin router's dependencies.

