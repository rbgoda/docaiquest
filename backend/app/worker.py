"""Arq worker.

Two tasks today:
  * `ingest_document_task(document_pk, tenant_id)` — parse PDF, chunk, embed,
    write `document_chunks` rows. On success, enqueues the classifier.
  * `classify_document_task(document_pk, tenant_id)` — classify the doc, run
    fact extraction, and bootstrap the graph.

Tenant flows through task args, never the cookie — workers don't run inside
a request and have no cookie to read.

Run with: `arq app.worker.WorkerSettings`
"""

from __future__ import annotations

import logging
import os

from arq import cron
from arq.connections import RedisSettings

from app.agents.classifier import classify_document, persist as persist_classification
from app.agents import categorizer, fact_extractor
from app.config import get_settings
from app.db import SessionLocal, set_current_tenant
from app.feature_flags import is_enabled, get_int
from app.license import is_cloud
from app.graph import bootstrap as graph_bootstrap
from app.graph import reconcile as graph_reconcile
from app.ingestion import ingest_document
from app.jobs.reap_stuck import reap_stuck_ingest_task
from app.jobs.materialize_artifacts import materialize_artifacts_task
from app.orm import Document

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s · %(message)s")
# §A7 · keep secrets out of logs — httpx/httpcore log request URLs (Gemini's
# endpoint carries ?key=<api-key>). Silence to WARNING.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
log = logging.getLogger("docaiq.worker")


# ── DLQ (TODO #23) ────────────────────────────────────────────────────────
# Arq retries failed jobs up to `max_tries` times (configured below). When
# the final retry STILL fails, on_job_end pushes a JSON record onto the
# `docaiq:dlq` Redis list so the operator can review/replay them. Without
# this, permanently-failing jobs just vanish after their last retry and
# nobody knows.

_DLQ_KEY = "docaiq:dlq"
_DLQ_MAX_LEN = 1000  # bounded — older entries get trimmed on push


async def _on_job_end(ctx: dict) -> None:
    """Arq's `on_job_end` hook. Runs after every job completion (success
    or failure). When the job ended in failure AND this was its final
    allowed try, write a DLQ entry."""
    import json as _json
    import time as _time

    job_try = int(ctx.get("job_try") or 1)
    max_tries = int(ctx.get("max_tries") or WorkerSettings.max_tries)
    score = ctx.get("score")
    function = ctx.get("function") or "unknown"

    # The Arq context only sets "exc_info" / "result" via job_results;
    # at on_job_end we only know the try count + whether we're at the cap.
    # That's enough — anything that hits max_tries is a poison pill.
    if job_try < max_tries:
        return  # will retry — not a DLQ candidate yet
    redis = ctx.get("redis")
    if redis is None:
        return
    entry = _json.dumps({
        "function": function,
        "job_id": ctx.get("job_id"),
        "job_try": job_try,
        "max_tries": max_tries,
        "enqueue_time": score,
        "failed_at": _time.time(),
        "tenant_hint": _tenant_from_args(ctx),
        # M44.P9.14 · capture args so replay endpoint can re-enqueue.
        # Best-effort JSON encoding · skip non-serializable args.
        "args": _safe_jsonable(ctx.get("args")),
    })
    try:
        await redis.lpush(_DLQ_KEY, entry)
        await redis.ltrim(_DLQ_KEY, 0, _DLQ_MAX_LEN - 1)
        log.error(
            "DLQ: %s exhausted %d retries — pushed to %s",
            function, max_tries, _DLQ_KEY,
        )
    except Exception as e:  # noqa: BLE001 — never break the worker over DLQ
        log.warning("DLQ push failed: %s", e)


def _tenant_from_args(ctx: dict) -> str | None:
    """Most of our jobs take (document_pk, tenant_id) — extract for the
    DLQ entry so the operator can route the investigation by tenant."""
    args = ctx.get("args") or ()
    if len(args) >= 2 and isinstance(args[1], str):
        return args[1]
    return None


def _safe_jsonable(value):
    """Convert anything to a JSON-safe shape. Used for DLQ args."""
    if value is None:
        return None
    import json as _json
    try:
        _json.dumps(value)
        return value
    except (TypeError, ValueError):
        # Convert via repr as a last resort · args can be re-enqueued
        # but won't deserialize back to the original objects.
        return [repr(v)[:200] for v in (value if isinstance(value, (list, tuple)) else [value])]


async def ingest_document_task(ctx, document_pk: int, tenant_id: str) -> dict:
    with SessionLocal() as session:
        result = ingest_document(session, document_pk, tenant_id)

    # Chain · ingest → classify. Classify runs synchronously so its output
    # is on the row before downstream tasks fire.
    pool = ctx.get("redis")
    if pool is not None:
        await pool.enqueue_job("classify_document_task", document_pk, tenant_id)
    return result


async def classify_document_task(ctx, document_pk: int, tenant_id: str) -> dict:
    """M11.6 · classify the doc, then (layer 1 of structured facts) run the
    text-based fact extractor for its type. Both are best-effort: failure
    here just means the chat path falls back to retrieval — no documents are
    lost. The fact extractor only fires when classifier confidence ≥ 0.5 and
    the doc_type maps to a fact schema (agreements, invoices, receipts, bank
    statements, policies, certificates today). Other types are skipped."""
    log = logging.getLogger("docaiq.classify")
    facts_persisted = False
    classified_type: str | None = None
    with SessionLocal() as session:
        set_current_tenant(tenant_id)
        # Idempotency — Arq retries this task up to max_tries on a late failure
        # (e.g. a transient error after extraction). Skip the EXPENSIVE LLM steps
        # whose output already persisted on a prior attempt so a retry doesn't
        # re-pay for fact extraction / vision.
        _doc0 = session.get(Document, document_pk)
        _ef0 = (_doc0.extracted_fields or {}) if _doc0 else {}
        _already_extracted = bool(isinstance(_ef0, dict) and _ef0.get("fields"))
        _already_vision = bool(isinstance(_ef0, dict) and _ef0.get("vision"))
        try:
            result = classify_document(session, document_pk)
            if result is not None:
                persist_classification(session, document_pk, result)
                classified_type = result.top.doc_type
                # Layer-1 structured facts. Conf gate prevents wasting an
                # LLM call on a doc the classifier barely recognises.
                if result.top.confidence >= 0.5 and not _already_extracted:
                    try:
                        fx = fact_extractor.extract(
                            session,
                            document_pk=document_pk,
                            classifier_doc_type=result.top.doc_type,
                        )
                        if fx is not None:
                            doc = session.get(Document, document_pk)
                            if doc is not None:
                                # M47 · Preserve text_layer from ingestion
                                _old = doc.extracted_fields or {}
                                _tl = _old.get("text_layer")
                                doc.extracted_fields = fx.to_jsonb()
                                if _tl:
                                    doc.extracted_fields["text_layer"] = _tl
                                session.commit()
                                facts_persisted = True
                                log.info(
                                    "fact_extractor: doc pk=%s → %s, conf=%.2f, %d top-level fields",
                                    document_pk, fx.schema_key, fx.confidence, len(fx.fields),
                                )
                    except Exception as e:  # noqa: BLE001 — best-effort
                        log.warning(
                            "fact_extractor: failed for doc pk=%s tenant=%s · %s",
                            document_pk, tenant_id, e,
                        )

            # P9.4 · vision-aware extraction. For image-heavy (image MIME) or
            # low-confidence docs, a vision read of page 1 captures signature /
            # stamp / checkbox / table / photo signals the flat OCR text loses.
            # Merged into extracted_fields.vision (+ a few promoted booleans the
            # anomaly validators + UI can use). Best-effort; never blocks.
            try:
                _s = get_settings()
                doc = session.get(Document, document_pk)
                conf = result.top.confidence if result is not None else 0.0
                mime = (doc.mime_type or "").lower() if doc else ""
                wants_vision = (mime.startswith("image/") or conf < _s.vision_extract_confidence_threshold) and not _already_vision
                if doc and doc.s3_key and _s.vision_extract_enabled and wants_vision:
                    import io as _io
                    import copy as _copy
                    from app import storage as _storage
                    from app import ingestion_vision as _iv
                    buf = _io.BytesIO()
                    for _ch in _storage.stream_object(doc.s3_key):
                        buf.write(_ch)
                    vfields = _iv.vision_extract_fields(
                        buf.getvalue(), doc.mime_type or "", db=session, tenant_id=tenant_id,
                    )
                    if vfields:
                        ef = _copy.deepcopy(doc.extracted_fields or {})
                        ef["vision"] = vfields
                        f = ef.get("fields") or {}
                        for _k in ("signature_present", "stamp_or_seal_present", "photo_present", "has_tables"):
                            if isinstance(vfields.get(_k), bool):
                                f.setdefault(_k, vfields[_k])
                        ef["fields"] = f
                        doc.extracted_fields = ef
                        from sqlalchemy.orm.attributes import flag_modified
                        flag_modified(doc, "extracted_fields")
                        session.commit()
                        log.info(
                            "vision-extract: doc pk=%s augmented (conf=%.2f mime=%s)",
                            document_pk, conf, mime,
                        )
            except Exception as e:  # noqa: BLE001 — best-effort
                log.warning("vision-extract: failed for doc pk=%s tenant=%s · %s", document_pk, tenant_id, e)

            # G10 · figure/chart extraction (opt-in, gated on the flag). For PDFs
            # with figure-bearing pages, pull chart data into extracted_fields.figures.
            try:
                _s2 = get_settings()
                doc = session.get(Document, document_pk)
                mime2 = (doc.mime_type or "").lower() if doc else ""
                if (doc and doc.s3_key and is_enabled("documents_figure_extraction", False)
                        and not (doc.extracted_fields or {}).get("figures")
                        and ("pdf" in mime2 or (doc.name or "").lower().endswith(".pdf"))):
                    import copy as _copy
                    import io as _io2
                    from app import storage as _st2
                    from app import ingestion_vision as _iv2
                    buf2 = _io2.BytesIO()
                    for _ch in _st2.stream_object(doc.s3_key):
                        buf2.write(_ch)
                    figs = _iv2.extract_figures(
                        buf2.getvalue(), max_pages=get_int("documents_figure_max_pages", 8),
                        db=session, tenant_id=tenant_id)
                    if figs:
                        ef2 = _copy.deepcopy(doc.extracted_fields or {})
                        ef2["figures"] = figs
                        doc.extracted_fields = ef2
                        from sqlalchemy.orm.attributes import flag_modified
                        flag_modified(doc, "extracted_fields")
                        session.commit()
                        log.info("figure-extract: doc pk=%s → %d figure(s)", document_pk, len(figs))
            except Exception as e:  # noqa: BLE001 — best-effort
                log.warning("figure-extract: failed for doc pk=%s tenant=%s · %s", document_pk, tenant_id, e)

            # Categorize transactions (bank/CC statements) + receipt items.
            # Adds a `category` field to each transaction in extracted_fields.
            # Uses the per-tenant merchant cache → free for repeat merchants.
            if facts_persisted:
                try:
                    import copy as _copy
                    doc = session.get(Document, document_pk)
                    if doc and doc.extracted_fields:
                        # Deep-copy because JSONB columns don't auto-detect
                        # nested mutations. Reassigning to a fresh object
                        # forces the dirty-check + UPDATE on commit.
                        ef = _copy.deepcopy(doc.extracted_fields)
                        fields = ef.get("fields") or {}
                        # Pick categorizer mode by the doc's type. Revenue
                        # docs (income side) get INCOME_CATEGORIES; everything
                        # else falls back to the auto-by-direction logic.
                        income_types = {"revenue_invoice", "customer_payment", "sales_receipt"}
                        cat_mode = "income" if classified_type in income_types else "auto"
                        vendor_pk = doc.vendor_pk if doc else None
                        for key in ("top_transactions", "items", "line_items"):
                            arr = fields.get(key)
                            if isinstance(arr, list) and arr:
                                r = categorizer.categorize_transactions(
                                    session, tenant_id, arr, mode=cat_mode,
                                    vendor_pk=vendor_pk,
                                )
                                log.info(
                                    "categorizer: doc pk=%s %s mode=%s → %d categorized (cache hits=%d, llm=%s)",
                                    document_pk, key, cat_mode, r.categorized, r.cached_hits, r.llm_called,
                                )
                        ef["fields"] = fields
                        doc.extracted_fields = ef
                        from sqlalchemy.orm.attributes import flag_modified
                        flag_modified(doc, "extracted_fields")
                        session.commit()
                except Exception as e:  # noqa: BLE001
                    log.warning(
                        "categorizer: failed for doc pk=%s tenant=%s · %s",
                        document_pk, tenant_id, e,
                    )

            # L3.2 · bootstrap the graph from the fresh fact-extracted blob.
            # Idempotent — re-runs replace prior bootstrap entries for this
            # doc. Best-effort: graph failure doesn't block ingestion.
            if facts_persisted:
                try:
                    result = graph_bootstrap.run(session, document_pk)
                    session.commit()
                    log.info(
                        "graph.bootstrap: doc pk=%s → %d entities, %d relations",
                        document_pk, result["entities_added"], result["relations_added"],
                    )
                except Exception as e:  # noqa: BLE001
                    log.warning(
                        "graph.bootstrap: failed for doc pk=%s tenant=%s · %s",
                        document_pk, tenant_id, e,
                    )

                # Step 3 · refresh the durable cross-document entity identities for
                # this owner from the now-current mentions. DERIVED + idempotent, so
                # it self-heals after any re-extraction. Best-effort.
                try:
                    from app.graph import identity_resolver
                    doc = session.get(Document, document_pk)
                    n = identity_resolver.rebuild_for_owner(
                        session, tenant_id, getattr(doc, "owner_user_id", None))
                    session.commit()
                    log.info("entity_identity: doc pk=%s → %d durable identities", document_pk, n)
                except Exception as e:  # noqa: BLE001
                    session.rollback()
                    log.warning("entity_identity: rebuild failed for doc pk=%s · %s", document_pk, e)

            # Reconciliation pass · only worth running when the freshly-
            # ingested doc is a receipt or bank statement (those are the
            # types whose entities feed duplicate detection + 3-way match).
            # Other types skip the scan so we don't churn the graph_runs
            # ledger on every upload.
            if facts_persisted and classified_type in (
                "receipt", "expense_claim", "bank_statement", "audited_financial_statement"
            ):
                try:
                    doc = session.get(Document, document_pk)
                    vendor_pk = doc.vendor_pk if doc else None
                    result = graph_reconcile.scan(session, vendor_pk=vendor_pk)
                    session.commit()
                    log.info(
                        "graph.reconcile: vendor_pk=%s → %d dup pairs, %d payment matches",
                        vendor_pk, result["duplicates"], result["payments"],
                    )
                except Exception as e:  # noqa: BLE001
                    log.warning(
                        "graph.reconcile: failed for doc pk=%s tenant=%s · %s",
                        document_pk, tenant_id, e,
                    )

            # M28 · auto-approve eligible docs when the tenant's threshold
            # is configured. Runs AFTER reconcile so the duplicate-detection
            # signal is up to date (a doc flagged as duplicate must never
            # auto-approve). No-op when documentAutoApprove is None.
            if facts_persisted:
                try:
                    from app.document_review import (
                        get_document_threshold, get_duplicate_doc_ids,
                        try_auto_approve,
                    )
                    threshold = get_document_threshold(session)
                    if threshold is not None:
                        doc = session.get(Document, document_pk)
                        if doc is not None:
                            dup_ids = get_duplicate_doc_ids(session)
                            flipped = try_auto_approve(
                                session, doc,
                                threshold=threshold,
                                duplicate_doc_ids=dup_ids,
                            )
                            if flipped:
                                session.commit()
                                log.info(
                                    "auto-approve: doc pk=%s tenant=%s flipped to reviewed",
                                    document_pk, tenant_id,
                                )
                except Exception as e:  # noqa: BLE001
                    log.warning(
                        "auto-approve: failed for doc pk=%s tenant=%s · %s",
                        document_pk, tenant_id, e,
                    )
        except Exception as e:  # noqa: BLE001 — classifier is best-effort
            log.warning(
                "classify_document_task: failed for doc pk=%s tenant=%s · %s",
                document_pk, tenant_id, e,
            )

    pool = ctx.get("redis")
    if pool is not None:
        # M44.P4 · materialize persistent doc artifacts (markdown / summary /
        # JSON / entities / TOC). Strategy is size-gated inside the task.
        await pool.enqueue_job(
            "materialize_artifacts_task", document_pk, tenant_id,
        )
        _s = get_settings()
        # M46 · self-learning classification. When the closed-enum classifier
        # landed on 'other'/nothing, reconcile the type from the doc's own AI
        # read (Phase 1). Cheap — the task early-returns if already confident.
        if _s.product == "documents" and (classified_type in (None, "other")):
            await pool.enqueue_job("reconcile_type_task", document_pk, tenant_id)
    return {
        "document_pk": document_pk,
        "classified": classified_type is not None,
        "doc_type": classified_type,
        "facts_persisted": facts_persisted,
    }


async def reconcile_type_task(ctx, document_pk: int, tenant_id: str) -> dict:
    """M46 · self-learning classification · derive an open-vocab doc type from
    the doc's own AI summary when the classifier returned 'other'/low-conf."""
    from app.documents_scope import set_current_owner_user_pk
    from app.repositories import documents as _repo
    from app.services.type_reconciler import reconcile_doc
    set_current_tenant(tenant_id)
    new_type = None
    reextracted = False
    with SessionLocal() as session:
        doc = _repo.get_row_by_pk(session, document_pk, tenant_id=tenant_id)
        if doc is not None:
            set_current_owner_user_pk(doc.owner_user_id)
            try:
                new_type = reconcile_doc(session, doc)
                # The first extraction ran against the 'other'→universal schema (the
                # reconciler fires AFTER classify+extract). If the reconciled type now
                # routes to a curated schema (e.g. résumé, or an approved library
                # schema), re-extract so the structured fields actually land — otherwise
                # the curated schema is defined but never applied to reconciled docs.
                if new_type and fact_extractor.would_use_curated_schema(session, new_type):
                    stored = (doc.extracted_fields or {}).get("doc_type") if isinstance(doc.extracted_fields, dict) else None
                    if stored != new_type:
                        fx = fact_extractor.extract(
                            session, document_pk=document_pk, classifier_doc_type=new_type)
                        if fx is not None and fx.schema_key != "universal":
                            fresh = session.get(Document, document_pk)
                            if fresh is not None:
                                fresh.extracted_fields = fx.to_jsonb()
                                session.commit()
                                reextracted = True
                                logging.getLogger("docaiq.worker").info(
                                    "reconcile_type_task: re-extracted pk=%s with curated schema %r",
                                    document_pk, fx.schema_key)
            except Exception as e:  # noqa: BLE001 — best-effort
                logging.getLogger("docaiq.worker").warning(
                    "reconcile_type_task failed for pk=%s: %s", document_pk, e)
            finally:
                set_current_owner_user_pk(None)
    return {"document_pk": document_pk, "reconciled_to": new_type, "reextracted": reextracted}


def _redis_settings() -> RedisSettings:
    url = get_settings().redis_url
    return RedisSettings.from_dsn(url)


def __warm_llm_overrides(db) -> None:
    """Apply DB-stored built-in provider keys onto the settings singleton.

    Mirrors llm_admin.apply_overrides() but inlined here so the worker can
    warm its settings without importing llm_admin (which pulls in newer ORM
    models that may not exist in the container image yet — docker cp deploy
    pattern only updates worker.py, not the image)."""

    import base64 as _b64
    import hashlib as _hashlib

    from app.orm import LlmProviderConfig
    from app.config import get_settings

    # Keep in sync with llm_admin.PROVIDER_KEY_ATTR.
    PROVIDER_KEY_ATTR = {
        "openrouter": "openrouter_api_key",
        "anthropic": "anthropic_api_key",
        "google": "google_genai_api_key",
        "dashscope": "dashscope_api_key",
        "openai": "openai_api_key",
        "ollama": "ollama_api_key",
        "deepseek": "deepseek_api_key",
    }

    s = get_settings()

    def _decrypt(enc: str) -> str:
        try:
            from cryptography.fernet import Fernet
            secret = (s.jwt_secret or "docaiq-dev-insecure").encode("utf-8")
            key = _b64.urlsafe_b64encode(_hashlib.sha256(
                secret + b"docaiq-llm-key").digest())
            return Fernet(key).decrypt(enc.encode("ascii")).decode("utf-8")
        except Exception:  # noqa: BLE001
            return ""

    try:
        from app.db import get_current_tenant
        rows = db.query(LlmProviderConfig).filter(
            LlmProviderConfig.tenant_id == get_current_tenant()
        ).all()
    except Exception:  # noqa: BLE001
        logging.getLogger("docaiq.worker").warning(
            "__warm_llm_overrides: DB query failed (non-fatal) — "
            "worker will use env-only keys for this lifetime")
        return

    applied = 0
    for r in rows:
        attr = PROVIDER_KEY_ATTR.get(r.provider)
        if not attr:
            continue
        if not r.enabled:
            setattr(s, attr, "")
        elif r.api_key_enc:
            decrypted = _decrypt(r.api_key_enc)
            if decrypted:
                setattr(s, attr, decrypted)
                applied += 1
            else:
                # Decrypt failed (key rotation, corruption) — keep env key
                # instead of silently blanking a working provider.
                logging.getLogger("docaiq.worker").warning(
                    "__warm_llm_overrides: decrypt failed for %r — "
                    "keeping env key", r.provider)
        # else: keep env-var value — don't overwrite

    if applied:
        logging.getLogger("docaiq.worker").info(
            "__warm_llm_overrides: applied %d provider override(s)", applied)


async def refresh_config_task(ctx) -> dict:
    """Cron: re-warm the worker's settings singleton + caches from DB.

    Runs every 5 min so admin-console changes (API keys, feature flags,
    embedding config) propagate to the worker without a restart. Each
    warm is self-contained and best-effort — a failure in one doesn't
    block the others and never kills the cron."""
    from app.db import SessionLocal as _SL, set_current_tenant as _sct

    s = get_settings()
    _sct(s.tenant_id)
    try:
        with _SL() as session:
            __warm_llm_overrides(session)
            for label, warm_fn in [
                ("feature_flags", None),
                ("embeddings", None),
            ]:
                try:
                    if label == "feature_flags":
                        from app.feature_flags import _refresh_feature_flags_cache
                        _refresh_feature_flags_cache(session)
                    elif label == "embeddings":
                        from app.embeddings import _refresh_embedding_config_cache
                        _refresh_embedding_config_cache(session)
                except Exception:
                    pass  # best-effort — log in the warm fn itself
    except Exception:
        pass  # DB down? skip this cycle, try again in 5 min
    finally:
        _sct(None)
    return {"status": "ok"}


async def _on_startup(ctx: dict) -> None:
    """Best-effort boot warm: apply DB-stored LLM provider keys + refresh the
    feature-flag / embedding-config caches so the worker sees the same config
    as the backend. Non-fatal — a failure here must never block the worker
    from starting."""
    # Fail fast if the embedding model's native width ≠ DOCAIQ_EMBED_DIM. The
    # worker is the ingestion writer — a mismatch here silently corrupts every
    # vector it writes, so this MUST raise.
    from app.embeddings import assert_embed_dim
    assert_embed_dim()

    # Apply DB-stored LLM provider keys onto the settings singleton, same as
    # the backend boot (main.py:103). Without this, API keys managed from the
    # admin console are invisible to the worker — gateway + vision pipeline
    # both read keys from `settings.<provider>_api_key`.
    # Inlined rather than importing llm_admin because the container image may
    # be older than the llm_admin module (docker cp deploy pattern).
    s = get_settings()
    set_current_tenant(s.tenant_id)
    try:
        with SessionLocal() as session:
            __warm_llm_overrides(session)
            # Warm caches the worker's LLM traffic depends on, same as the
            # backend boot (main.py:110-130). PII config lives in superadmin
            # (imports FastAPI routers) — deferred. get_cached_pii_config()
            # returns PII_DEFAULTS on a cold cache with NO lazy DB read, so
            # the worker runs with default redaction until the next restart
            # that has a warm cache. Operator PII changes in the admin console
            # only apply to the backend process.
            try:
                from app.feature_flags import _refresh_feature_flags_cache
                _refresh_feature_flags_cache(session)
            except Exception:  # noqa: BLE001
                logging.getLogger("docaiq.worker").warning(
                    "feature_flags: cache warm at boot failed (non-fatal)")
            try:
                from app.embeddings import _refresh_embedding_config_cache
                _refresh_embedding_config_cache(session)
            except Exception:  # noqa: BLE001
                logging.getLogger("docaiq.worker").warning(
                    "embeddings: config cache warm at boot failed (non-fatal)")
    except Exception:  # noqa: BLE001
        logging.getLogger("docaiq.worker").warning(
            "__warm_llm_overrides: boot warm failed (non-fatal)")
    finally:
        set_current_tenant(None)


async def reextract_type_task(ctx, type_slug: str, tenant_id: str) -> dict:
    """Re-extract every ready document whose type resolves to `type_slug` — fired on schema approval
    so the typed schema is applied (extract → persist → graph re-bootstrap). Idempotent + per-doc
    error-isolated; docs whose type doesn't resolve to this slug are skipped."""
    from sqlalchemy import select
    from app.orm import Document
    from app.agents import fact_extractor
    from app.agents.fact_extractor import _resolve_schema_slug
    from app.graph import bootstrap as graph_bootstrap
    log = logging.getLogger("docaiq.worker")
    n = 0
    with SessionLocal() as session:
        set_current_tenant(tenant_id)
        docs = session.scalars(select(Document).where(
            Document.tenant_id == tenant_id, Document.ingestion_status == "ready")).all()
        for d in docs:
            try:
                if _resolve_schema_slug(session, d.doc_type or "") != type_slug:
                    continue
                fx = fact_extractor.extract(session, document_pk=d.pk,
                                            classifier_doc_type=d.doc_type or "other")
                if fx is not None:
                    d.extracted_fields = fx.to_jsonb()
                    session.commit()
                    try:
                        graph_bootstrap.run(session, d.pk)
                        session.commit()
                    except Exception:  # noqa: BLE001
                        session.rollback()
                    n += 1
            except Exception as e:  # noqa: BLE001 — isolate one bad doc
                session.rollback()
                log.warning("reextract_type_task: doc pk=%s failed: %s", d.pk, e)
    log.info("reextract_type_task: slug=%s re-extracted %d docs (tenant=%s)", type_slug, n, tenant_id)
    return {"type_slug": type_slug, "reextracted": n}


class WorkerSettings:
    functions = [
        ingest_document_task,
        classify_document_task,
        reconcile_type_task,
        reextract_type_task,
        materialize_artifacts_task,
        reap_stuck_ingest_task,
    ]
    # Periodic config refresh — re-warms the settings singleton from DB so
    # admin-console key/flag changes propagate to the worker without a restart.
    # Runs every 5 min at :03/:08/:13/… (off the :00/:30 fleet stampede).
    cron_jobs = [
        cron(refresh_config_task, minute={3, 8, 13, 18, 23, 28, 33, 38, 43, 48, 53, 58}),
        # M49 · reap docs stuck in pending/processing (crash recovery) every 10 min.
        cron(reap_stuck_ingest_task, minute={4, 14, 24, 34, 44, 54}),
    ]
    redis_settings = _redis_settings()
    # Per-task safety net. Real ingestion of a 50MB PDF + OpenAI embedding
    # round-trips can take a minute; 5 minutes is enough headroom without
    # masking a true hang. The LLM cascade timeout keeps per-call latency
    # comfortably under this cap for typical fixtures.
    job_timeout = int(os.environ.get("DOCAIQ_WORKER_JOB_TIMEOUT", "300") or "300")
    # RAG-roadmap #1 · throughput knob — concurrent jobs per worker process.
    # Env-tunable (default 4 = unchanged); raise it (or run more worker replicas)
    # to scale ingestion throughput without a code change.
    max_jobs = int(os.environ.get("DOCAIQ_WORKER_MAX_JOBS", "4") or "4")
    keep_result = 60 * 60
    # TODO #23 · bounded retry + DLQ. Arq defaults to 5 retries with
    # exponential backoff. We lower to 3 so true poison pills surface
    # within ~minutes (5 retries × 5min timeout = 25 min before DLQ),
    # and wire on_job_end to push exhausted jobs to docaiq:dlq.
    max_tries = 3
    on_job_end = _on_job_end
    on_startup = _on_startup
