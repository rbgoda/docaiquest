from functools import lru_cache
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


AuthProvider = Literal["dev", "google", "oidc"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DOCAIQ_", env_file=".env", extra="ignore")

    app_name: str = "DocAIQuest API"
    environment: str = "development"
    tenant_id: str = "default"

    # M46 · which product this stack serves. "auditing" = the full app
    # (documents + frameworks + matcher). "documents" = the standalone
    # per-user Documents System (document processing + chat, NO audit /
    # frameworks). Exposed via /api/auth/config so the frontend renders the
    # right shell. Backend routers are unchanged; the product flag only
    # selects the UI surface (the document pipeline is shared, audit/framework
    # endpoints simply go unused in documents mode).
    product: Literal["auditing", "documents"] = "auditing"

    # Deployment license mode: oss (open-source, self-hosted, BYO keys) |
    # cloud (DocAIQ-hosted premium with proxy). Gated by DOCAIQ_LICENSE_MODE.
    license_mode: Literal["oss", "cloud"] = "oss"

    # M47 · superadmin console. Comma-separated emails allowed to call the
    # /api/superadmin/* plan-management endpoints (list users, set plan, extend
    # trial). Documents product only. Empty = no superadmin (endpoints 403).
    documents_superadmin_emails: str = ""

    @property
    def superadmin_email_set(self) -> set[str]:
        return {e.strip().lower() for e in self.documents_superadmin_emails.split(",") if e.strip()}

    # M48 · transactional email (Resend) for signup verification. When the API
    # key is empty, the sender LOGS the link instead of sending (dev mode) so the
    # flow is testable with no provider configured.
    resend_api_key: str = ""
    email_from: str = "DocAIQuest <no-reply@docaiq.jicama.tech>"
    # Where "Contact us" form submissions are emailed. Empty → the message is only
    # logged (dev) / falls back to email_from's inbox. Set in prod .env.
    contact_email: str = ""
    # When True, unverified email/password users are blocked from logging in
    # until they confirm. Default False = allow login + show a verify banner
    # (softer; avoids lockouts if delivery hiccups during a launch).
    email_verification_required: bool = False

    # M49 · cross-app extraction API. The audit app calls /api/extraction/* with
    # this key (X-API-Key header) to reuse Documents' extraction intelligence for
    # its framework requirements. Empty = the extraction API is disabled (401).
    extraction_api_key: str = ""

    # Postgres connection string. The default is the docker-compose service.
    # Override via DOCAIQ_DATABASE_URL for local dev or per-tenant deployments.
    database_url: str = "postgresql+psycopg://docaiq:docaiq-dev@postgres:5432/docaiq"

    cors_origins: list[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]

    # ---- Auth ------------------------------------------------------------
    # `dev`    — email+password login allowed (development only).
    # `google` — Google OIDC only.
    # `oidc`   — generic OIDC (WorkOS/Auth0/Okta/Keycloak), config via OIDC_* vars.
    # `dev` accepts BOTH flows so you can iterate locally without GCP creds.
    auth_provider: AuthProvider = "dev"

    # Public URL the browser hits (for OAuth redirect_uri construction).
    # In compose this is the nginx host; in prod, the customer's ingress URL.
    public_url: str = "http://localhost:8080"

    # M37 · where the free-tier "Upgrade to dedicated" banner sends the user.
    # This is the public platform-UI signup form (paid plans) — submitting it
    # creates a pending SignupRequest the operator approves to provision a
    # dedicated container. Only meaningful on the shared free container; set
    # via env (prod: https://docaiq.jicama.tech/signup.html?plan=starter).
    upgrade_url: str = "http://localhost:9000/signup.html?plan=starter"

    # M43 · control-plane internal URL · used by the marketplace install
    # endpoint to fetch the framework catalog + record the install. In
    # docker compose this is `http://control_plane:8002` over the shared
    # network; in prod it's the localhost-bound 127.0.0.1:9001 or however
    # nginx routes /api/marketplace. Empty disables the marketplace.
    control_plane_internal_url: str = ""

    # ── M43.P1 · Contextual Retrieval + Re-ranker ────────────────────────
    # Anthropic's Contextual Retrieval pattern (Sep 2024): prepend ~50-100
    # token chunk-context to each chunk before embedding. Lifts retrieval
    # recall +35-49% on diverse corpora. Cost: 1 LLM call per doc + 1
    # call per chunk. With Qwen 2.5 via OpenRouter (user's 1M-token
    # budget) this is effectively free; with Gemini Flash, ~$0.005/doc.
    # Set false on tenants where the latency / cost matters more than
    # recall (e.g. low-stakes evaluation environments).
    contextual_retrieval_enabled: bool = True

    # BGE-Reranker-v2-m3 cross-encoder. Applied after the hybrid (BM25 +
    # cosine RRF) retrieve. Re-scores the top-K initial pool and returns
    # the actually-best top-N. Open source (BAAI), runs on CPU at ~20ms
    # per (query, chunk) pair. Lift: +25-40% precision@5 — BUT on CPU the 568M
    # cross-encoder is brutal: measured ~16s/query to rerank 20 candidates on an
    # ARM CPU container (the original "~400ms" note assumed a GPU). Both retrieval
    # halves (BM25 1ms + pgvector cosine 25ms) are fast, so with the reranker ON
    # it is ~940x of the chat latency. DEFAULT OFF — RRF over BM25+cosine with the
    # local MiniLM embeddings is the fast baseline (full retrieve ~17ms). Enable
    # only on a GPU deployment (DOCAIQ_RERANKER_ENABLED=true). This is corpus-size
    # independent either way: retrieval is indexed + only the top-K chunks reach
    # the LLM, so it scales to 1000s of docs.
    reranker_enabled: bool = False

    # Convert legacy MS Office (.doc/.xls/.ppt) + OpenDocument (.odt/.ods/.odp) +
    # RTF → PDF via headless LibreOffice, then parse as PDF. OPT-IN + default OFF:
    # LibreOffice is ~700MB-1GB (not in the slim image) and converting UNTRUSTED
    # docs is a real attack surface, so the convert path runs hardened (no macros,
    # isolated profile, no network, hard timeout, non-root) and only when soffice
    # is installed AND this is true. .pptx (python-pptx, pure-Python) is always on.
    office_convert_enabled: bool = False

    # Fleet (Enterprise dedicated containers). The CENTRAL (shared) instance is
    # the registry; a dedicated container sets fleet_admin_url + instance_id +
    # fleet_token to self-register and heartbeat. fleet_token gates the public
    # /api/sync/* endpoints. heartbeat_seconds = how often a member checks in.
    fleet_token: str = ""
    fleet_admin_url: str = ""          # set on a MEMBER → points at the central instance
    instance_id: str = ""             # set on a MEMBER → its unique id
    instance_name: str = ""
    fleet_heartbeat_seconds: int = 300
    fleet_offline_after_seconds: int = 900   # central marks a member offline past this

    # When the reranker is enabled, the hybrid layer fetches this many
    # candidates before re-ranking. 20 is the proven sweet spot — more
    # adds latency without meaningful precision gains, fewer caps the
    # reranker's working set.
    reranker_top_k_initial: int = 20

    # Recency-weighted retrieval. OFF by default — pure relevance is right for a
    # compliance/document corpus; enable for time-sensitive corpora (statements,
    # emails) or assistant-style use. A GENTLE multiplier on the final score:
    # factor = floor + (1-floor) * 0.5**(age_days/half_life), so a newer doc is
    # preferred on ties but old-but-relevant docs aren't buried (bounded by floor).
    retrieval_recency_enabled: bool = False
    retrieval_recency_half_life_days: float = 180.0
    retrieval_recency_floor: float = 0.5
    # #5 · per-user feedback boost. OFF by default — gently lift docs the owner
    # marked answers "helpful" on (demote "unhelpful"), bounded by strength.
    retrieval_feedback_boost_enabled: bool = False
    retrieval_feedback_boost_strength: float = 0.15
    # Auto-merging / neighbour context expansion (DocAIQuest-native "parent-child"
    # retrieval, no migration). Retrieval + rerank still operate on the precise
    # child chunk; we just hand the LLM that chunk PLUS its adjacent chunks in the
    # same document so an answer split across a chunk boundary isn't fragmented.
    # Only the returned Hit.text is widened — chunk_pk/page/bbox/score stay the
    # matched child's, so citations + click-to-highlight are unchanged. Off by
    # default (reversible via DOCAIQ_RETRIEVAL_CONTEXT_EXPANSION); window = chunks
    # on EACH side (1 → the hit + its two neighbours), capped to bound tokens.
    retrieval_metrics_enabled: bool = True  # M47 · toggleable via DOCAIQ_RETRIEVAL_METRICS_ENABLED
    retrieval_context_expansion: bool = False
    retrieval_context_window: int = 1
    retrieval_context_max_chars: int = 2400
    # RAG-roadmap #3 · GraphRAG in the retriever. OFF by default — union chunks whose
    # extracted ENTITIES (canonical/surface) match a distinctive query token into the
    # candidate pool (catches surface-form variants + typed IDs flat BM25/cosine miss).
    # Additive; off = today's chunk-only retrieval. A/B via DOCAIQ_GRAPH_RETRIEVAL_ENABLED.
    graph_retrieval_enabled: bool = True

    # M44.P2 · Document Agent ReAct loop. When enabled, doc-chat replaces
    # the single-shot validator + Critic-Refine path with a tool-using
    # agent (search_chunks / get_extracted_field / validate_id_format /
    # ...). Trace persists per step to agent_traces; the reviewer can
    # click "Show reasoning" on any answer. Default OFF — flip per-tenant
    # via env. Failing-open: if the agent raises, doc-chat falls back to
    # the legacy path so chat keeps working.
    agent_mode_enabled: bool = False

    # M44.P2.5 · DB-first reflexion cache. When a new question is
    # semantically close (cosine ≥ threshold) to a prior question whose
    # answer was marked helpful by a reviewer, return the cached answer
    # instead of calling the LLM. Zero LLM calls per cache hit. Safe by
    # construction — see app/reflexion_cache.py docstring.
    reflexion_cache_enabled: bool = True
    reflexion_cache_threshold: float = 0.92
    reflexion_cache_min_helpful: int = 1

    # M44.P10 · two-phase "delete with learning preservation". When True,
    # DELETE /documents/{id} runs the promotion engine (Phase 1) to lift
    # generalizable knowledge into the tenant UNDERSTANDING tables before
    # the cascade (Phase 2) purges the document's evidence. PR3 (2026-06-02)
    # wired the endpoint + flipped this ON by default; the open design
    # questions in docs/architecture/DELETE_WITH_LEARNING.md were settled with
    # the product owner (helpful>=2, type-patterns tenant-wide / names local,
    # telemetry on). Kept as a flag for one-line rollback until P10 PR4 drops
    # it. Set DOCAIQ_DELETE_WITH_LEARNING=false to revert to plain cascade.
    delete_with_learning: bool = True

    # M44.P13 · federated agentic learning governance (see
    # docs/architecture/FEDERATED_LEARNING.md). Two independent consent flags:
    #   contribute_learning   — may this tenant's ANONYMIZED skeletons (no
    #     values / PII / identities — see app/services/skeletonizer.py) feed
    #     the global knowledge pool? Opt-out model: default on; enterprise /
    #     regulated tenants can disable and still receive.
    #   receive_global_learning — may this tenant seed/sync curated global
    #     knowledge into its local UNDERSTANDING tables (source='global')?
    # PR2 wires the nightly knowledge_promoter (gated on contribute_learning +
    # control_plane_internal_url). receive_global_learning is still inert until
    # the seed/sync PR.
    contribute_learning: bool = True
    receive_global_learning: bool = True
    # Salt for the OPAQUE per-tenant token sent with contributions. The global
    # pool only ever stores this token (in staging), never the slug. Default
    # empty → token = sha256(tenant_id); set a deployment-wide secret salt so
    # the token isn't a bare slug hash. Stable per tenant (rotating it just
    # makes the aggregator count the tenant as new).
    knowledge_token_salt: str = ""

    # HS256 signing secret for our own session JWTs.
    # OVERRIDE IN PROD — anything ≥32 chars random. Docker compose injects via env.
    jwt_secret: str = "dev-only-do-not-use-in-prod-please-rotate-me-please"
    jwt_ttl_seconds: int = 60 * 60 * 8  # 8h

    # Google OIDC. Empty defaults disable Google login; set these via env to enable.
    google_client_id: str = ""
    google_client_secret: str = ""
    # Google Picker API key (DEVELOPER key, NOT the OAuth secret) — enables the
    # "Import from Drive" Picker so users can bring in pre-existing Drive files
    # within drive.file scope. Create in the SAME GCP project with the Picker API
    # enabled. Empty = the Import-from-Drive button stays hidden.
    google_picker_api_key: str = ""

    # Generic OIDC (future WorkOS/Auth0/etc swap-in). Empty disables.
    oidc_issuer: str = ""
    oidc_audience: str = ""
    oidc_jwks_url: str = ""

    # Session cookie name. Same across providers.
    session_cookie_name: str = "docaiq_session"
    # Set to ".docaiq.jicama.tech" to SHARE the session across subdomains (so a Google login on the
    # main app also authenticates admin.docaiq.jicama.tech). Empty = host-scoped (default). The
    # OAuth callback then honours a validated `next=` to land the user back on the admin console.
    session_cookie_domain: str = ""
    # AIQ Suite SSO — shared secret across every *.jicama.tech app. Trust anchor
    # for the `jicama_sso` JWT (browser cookie login + API Bearer). Set from the
    # suite secret in prod .env; empty = SSO off. Never commit the value.
    jicama_sso_secret: str = ""

    # ---- Shared SaaS mode (M37 · free tier) -----------------------------
    # When True, this backend serves MULTIPLE tenants from one container
    # (instead of the per-tenant model). Key behavior changes:
    #   - TenantMiddleware / get_current_user skip the 'JWT.org_id must ==
    #     settings.tenant_id' check (since many org_ids live here).
    #   - Login looks up user by email across all tenants in the DB and
    #     issues JWT with that user's actual tenant_id.
    #   - Plan-limit gates (enforce_plan_limits.py) refuse uploads/audits/
    #     LLM calls for plan_type='free' tenants past their caps.
    # OFF for dedicated per-tenant containers · they remain locked to
    # settings.tenant_id with full cookie-tenant binding.
    shared_mode: bool = False
    # Shared secret accepted by /api/internal/provision (control plane uses
    # this to insert new free signups into the shared container's DB).
    # Required when shared_mode=True; empty disables the endpoint.
    internal_provision_secret: str = ""

    # ---- Bootstrap owner (control-plane provisioning) -------------------
    # When set, the seed step creates exactly one owner user from these
    # values instead of the default elena/james/marcus dev trio. Used by
    # the SaaS control plane to inject the real customer-side owner when
    # a new tenant is provisioned from a signup approval. Empty in dev
    # (default + acme tenants), populated for every other tenant.
    bootstrap_owner_email: str = ""
    bootstrap_owner_name: str = ""
    bootstrap_owner_password: str = ""

    # ---- Seed demo data -------------------------------------------------
    # True (default) → seed.py loads the canonical vendor / audit-run /
    # audit-history fixture so dev tenants (default + acme) and local
    # demos have rich data out of the box.
    # False → minimal seed: owner user + routing_config + the requirement
    # catalog only. No vendors, no audit runs, no history. Use for
    # customer-provisioned tenants where the operator wants a clean
    # empty workspace they'll fill in themselves. Control plane sets
    # this to false for every signup-approved tenant.
    seed_demo_data: bool = True

    # ---- Object storage --------------------------------------------------
    # MinIO in dev, AWS S3 / GCS / Azure Blob in prod. Bucket is per-tenant.
    s3_endpoint: str = "http://minio:9000"
    s3_access_key: str = "docaiq"
    s3_secret_key: str = "docaiq-dev-secret-please-rotate"
    s3_bucket: str = "docaiq-default"
    s3_region: str = "us-east-1"
    # Upload caps. Real audit PDFs are typically < 50MB; reject obvious mistakes
    # at the API boundary rather than waiting until the worker chokes.
    max_upload_bytes: int = 100 * 1024 * 1024  # 100 MB

    # Hard ceiling on any list endpoint that does not yet implement the
    # `Paginated[T]` envelope. Without this, a 5k-doc tenant gets a
    # multi-MB JSON blob per call. Repos that respect this cap should
    # `.limit(get_settings().max_list_rows)` their SELECTs; routers
    # that wrap them can set an `X-Result-Truncated: true` response
    # header when the cap was hit. Real pagination is TODO #14b.
    max_list_rows: int = 1000

    # ---- Ingestion (M7) --------------------------------------------------
    redis_url: str = "redis://redis:6379/0"
    # Embedding backend (Reducto-parity G1). Default "local": the in-process
    # sentence-transformers MiniLM model — no API key, no egress, no per-call
    # cost, 384d native (= embed_dim) — so cosine half of hybrid RRF retrieval
    # is real out of the box (the old "hash" default has no semantic structure,
    # leaving retrieval BM25-only). Other options: "openai" / "gemini" /
    # "dashscope" (need the matching key), or "hash" (deterministic, deps-free,
    # dev/test only).
    #   ⚠️ Switching backends on an EXISTING workspace requires a one-time
    #   re-ingest — different models share the pgvector column dim but not the
    #   vector space, so old + new vectors are not comparable. embed_signature()
    #   stamps snapshots so a restore refuses to reuse mismatched vectors.
    embed_backend: str = "local"
    embed_dim: int = 384
    # Foundation-Fix-B · general-purpose entity extraction (NER). Controls the
    # ingestion entity pass:
    #   "regex" (default) — the deterministic app/entities.py pass only
    #     (money/ISO/control-IDs/dates/email — the compliance vocabulary).
    #   "llm"  — LLM NER over free text (people/orgs/locations/products/clauses/
    #     obligations/roles…) INSTEAD of regex → universal-document coverage.
    #   "both" — run regex AND LLM NER, deduped. Widest coverage, keeps regex
    #     precision on money/control-IDs.
    # LLM modes add ONE cheap call per document at ingest and are OFF by default
    # (cost) — enable + tune on a real multi-domain corpus with a cost sign-off,
    # like G10/G11. Routed model via DOCAIQ_NER_MODEL (default Haiku-class).
    ner_backend: str = "regex"
    # Move-1 PR3 · schema crystallization. When on, a nightly job distils stable
    # per-type LearnedSchema clusters into concrete GeneratedSchema rows and the
    # universal extractor promotes their labels to first-class fields. OFF by
    # default (ships dormant, like G10/G11); enable per corpus with review.
    schema_crystallize_enabled: bool = False
    schema_crystallize_min_docs: int = 5      # cluster must be seen ≥ this to crystallize
    schema_crystallize_core_ratio: float = 0.5  # a label is "core" if in ≥ this fraction of docs
    openai_api_key: str = ""
    openai_embed_model: str = "text-embedding-3-small"
    # Local (no-API-key) embedding model · 384d native, matches embed_dim.
    local_embed_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    # Retrieval Step 2 · dual-column embedder upgrade (BGE-M3, 1024d, 8192-ctx, Apache).
    # Written to document_chunks.embedding_v2 (migration 0094); retrieval flips to it when
    # BGE-M3 v2 (1024d, multilingual, 8192-ctx) is now the SOLE retrieval embedding.
    # v1 MiniLM (384d) is kept for backward compat but no longer queried (see retrieval.py).
    embed_v2_model: str = "BAAI/bge-m3"
    embed_v2_dim: int = 1024
    embed_v2_active: bool = True  # always on — v2 is the primary path
    # Which backend computes the v2 (1024d) embeddings. "local" = BGE-M3 via
    # sentence-transformers (needs GPU to be fast). "dashscope" = the hosted
    # text-embedding-v4 (1024d native) — CPU-friendly, no GPU, a few cents to
    # backfill. Measured on the real corpus to beat MiniLM-384 (recall@10 66→85%).
    embed_v2_backend: str = "local"
    # Chunking knobs for the parse stage. ~1000 chars roughly maps to ~200
    # tokens, a comfortable retrieval granule for compliance text.
    chunk_target_chars: int = 1000
    chunk_overlap_chars: int = 150
    # RAG-roadmap #4 · semantic (sentence-aware) chunking. OFF by default. The chunker
    # is already paragraph-block aware; this only changes how OVERSIZED blocks are cut —
    # snapping to the nearest sentence boundary instead of a hard char window. Affects
    # NEW ingests only (existing chunks unchanged). A/B via DOCAIQ_SEMANTIC_CHUNKING.
    semantic_chunking: bool = False
    # R6 · ingestion hardening. NFKC unicode normalization (safe, always-on by
    # default) + near-duplicate chunk dedup (drop recurring boilerplate so it
    # doesn't dominate retrieval; conservative 0.9 Jaccard, keeps first copy).
    chunk_nfkc_normalize: bool = True
    chunk_dedup_near_duplicates: bool = True
    chunk_dedup_threshold: float = 0.9
    # G6 · image auto-orientation. EXIF orientation is ALWAYS corrected (safe,
    # deterministic — fixes phone-photo rotation). Tesseract OSD rotation of
    # scans WITHOUT EXIF is OPT-IN and default-OFF: OSD can mis-detect an
    # already-upright page and wrongly rotate it, so it only runs when this is
    # enabled and only on a very high orientation confidence. Validate on a real
    # scan corpus before turning it on.
    ocr_osd_autorotate: bool = False
    ocr_osd_min_conf: float = 3.0

    # ---- LLM gateway (M9) ------------------------------------------------
    # API keys. Any/all may be empty. With no keys set, the gateway falls back
    # to the `stub` backend which returns canned responses (good for dev /
    # offline demos / CI). Each tier's models are matched to a backend by ID
    # prefix in app/llm/gateway.py — see registry there for the mapping.
    openrouter_api_key: str = ""
    anthropic_api_key: str = ""
    google_genai_api_key: str = ""
    # DeepSeek API (platform.deepseek.com). Used for markdown post-processing.
    # OpenAI-compatible endpoint at https://api.deepseek.com/v1.
    deepseek_api_key: str = ""
    # Alibaba Dashscope (qwen3-vl-235b-a22b and other Qwen-VL models).
    # Direct API · separate quota from OpenRouter. Sign up at
    # dashscope-intl.aliyuncs.com (or dashscope.console.aliyun.com for CN).
    # Model prefix `dashscope/` in routing config routes here.
    dashscope_api_key: str = ""
    # OpenAI-compatible BASE URL · gateway appends /chat/completions.
    # Drop `-intl` from the host for CN-side accounts.
    dashscope_base_url: str = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
    # Ollama — local LLM runner, OpenAI-compatible API.
    # Defaults to localhost:11434; override for remote/Docker setups.
    # Model prefix `ollama/` in routing config routes here.
    ollama_base_url: str = "http://localhost:11434"
    ollama_api_key: str = ""  # optional — only needed behind an auth proxy
    # M44.P9.1 · Dashscope embeddings model (reused when
    # DOCAIQ_EMBED_BACKEND=dashscope). v4 is the current best; v3 still
    # works. Both accept a `dimensions` parameter for OpenAI-style sizing.
    dashscope_embed_model: str = "text-embedding-v4"
    # M46 · Qwen-VL model for the PAID vision-OCR tier (the escalation when the
    # free Gemini tier fails). qwen-vl-max is the strong default; switch to
    # qwen3-vl-235b-a22b for the hardest scans via DOCAIQ_VISION_QWEN_MODEL.
    vision_qwen_model: str = "qwen-vl-max"
    # Phase 3 · the "Re-analyze with the best model" action (and future auto-escalation for
    # new/low-confidence docs) runs extraction on this model. Defaults to the strong tier;
    # point it at a bigger model or a Claude/OpenAI id via DOCAIQ_STRONG_EXTRACT_MODEL once a
    # funded key is wired. Re-uses the same gateway routing as the default extractor.
    strong_extract_model: str = "qwen-max"

    # Merchant/transaction categorizer (app/agents/categorizer.py). MUST be a funded provider
    # (routes through the gateway) — the old hardcoded OpenRouter model 402'd on a depleted key,
    # sending every merchant to "Other". Override via DOCAIQ_DOCUMENTS_CATEGORIZE_MODEL.
    documents_categorize_model: str = "dashscope/qwen-max"

    # Intelligence / suggest-views engine (dashboard widget proposals). MUST be a funded
    # provider. Override via DOCAIQ_INTELLIGENCE_MODEL.
    intelligence_model: str = "dashscope/qwen-max"

    # Adaptive Schema Loop (schema_autopilot): auto-draft a schema for underserved docs (no schema,
    # or typed but poorly extracted), escalating to a stronger model. Drafts land `proposed` for HITL
    # review; the on-approval trigger re-extracts on approve. `_model` empty → strong_extract_model
    # (point it at a frontier id via DOCAIQ_SCHEMA_AUTOPILOT_MODEL once a funded key is wired).
    schema_autopilot_enabled: bool = True
    schema_autopilot_model: str = ""
    schema_autopilot_min_coverage: float = 0.4   # typed doc below this coverage = underserved

    # General-assistant fallback: when a chat question is OFF-TOPIC (not about the user's documents
    # and no evidence retrieved) — e.g. "what's the weather", general knowledge — answer it with the
    # LLM instead of a dead "not found". `_model` empty → strong_extract_model (qwen via DashScope);
    # set to a free OpenRouter model to use those, e.g. `openrouter/deepseek/deepseek-chat:free` or
    # `openrouter/google/gemini-2.0-flash-exp:free` (needs DOCAIQ_OPENROUTER_API_KEY). No real-time /
    # web access — the prompt tells the model to say so.
    documents_general_fallback_enabled: bool = True
    documents_general_fallback_model: str = ""

    # M44.P11 · GDPR / PDPA / PII safety on LLM calls.
    # See docs/architecture/PII_LLM_SAFETY.md for full design.
    #
    # pii_redact_before_llm · OFF by default until smoke-tested per tenant.
    # When ON, the gateway redacts PII (emails, phones, IDs, names from
    # the entities table) BEFORE sending to the LLM, then detokenizes
    # the response back to original values for the user.
    pii_redact_before_llm: bool = False
    # pii_redact_person_names · OFF by default. Person NAMES are the primary key
    # users search/organize by ("find documents for Kalyani Goda"), so redacting
    # them breaks search — the question's name and the doc's name become
    # different placeholder tokens the model can't match. We keep masking the
    # genuinely sensitive identifiers (NRIC, passport, account #, card, IBAN,
    # email, phone, DOB) regardless; this switch ONLY controls person/org names.
    # Turn ON for maximal compliance (accepting that name search degrades).
    pii_redact_person_names: bool = False
    # pii_protect_at_rest · OFF by default. When ON, the ingestion pipeline
    # tokenizes PII (cards, IBAN, passport, NRIC, SSN, etc.) inside the text
    # we store in our OWN database (document_chunks + extracted_fields) and
    # keeps the real values only in the encrypted `pii_vault` table. Stored
    # docs show placeholders; an owner/admin/reviewer can REVEAL a specific
    # document (detokenize) via /api/documents/{id}/pii/reveal. New uploads
    # are protected by default; existing docs are untouched until re-ingested.
    pii_protect_at_rest: bool = False
    # P9.4 · vision-aware extraction. When on, image-heavy or low-confidence
    # docs get an extra vision-mode read of page 1 (signature/stamp/checkbox/
    # table/photo presence + salient fields the flat OCR text loses), merged
    # into extracted_fields.vision. Gated by the routing conditions below so
    # it only fires where it adds value — cost stays bounded.
    vision_extract_enabled: bool = True
    vision_extract_confidence_threshold: float = 0.7
    # G10 · figure/chart extraction via VLM. OFF by default — it adds a vision
    # call per figure-bearing page (cost). Enable per-tenant when chart-heavy docs
    # justify it: DOCAIQ_DOCUMENTS_FIGURE_EXTRACTION=true.
    documents_figure_extraction: bool = False
    documents_figure_max_pages: int = 8
    # Cost lever: max image edge (px) sent to the VLM for figure extraction.
    # Lower = fewer input tokens (charts read fine at ~1280; OCR path uses 1568).
    documents_figure_max_edge: int = 1280
    # G12 · Max pages to process via vision OCR per document (default 100).
    # Pages beyond this get a truncation marker. Set 0 for no limit.
    documents_max_ocr_pages: int = 100
    # G13 · Use Docling (MIT) for PDF parsing when available. Handles multi-column
    # layouts, complex tables, figures, and reading order natively — no CV vocabulary
    # gate. Falls back to PyMuPDF if docling is not installed or on error.
    documents_docling_enabled: bool = False
    # M47 · Pipeline version — bumped on parser/chunker/embedder changes. New chunks
    # get this version. Stale-chunk detection uses it for targeted re-processing.
    pipeline_version: int = 2  # v2: Docling + Camelot + BGE-M3 + IR blocks for all formats
    # M47 · Indexing quality critic — LLM evaluates chunk coherence, entity accuracy,
    # language handling, and searchability after ingestion. 1-3 cheap LLM calls/doc.
    documents_indexing_critic: bool = False
    # M47 · LLM bbox fallback — when page.search_for() can't find a field value,
    # ask Qwen-VL to locate it on the page image. ~$0.001 per page.
    documents_llm_bbox_fallback: bool = False
    # Embedded-image OCR for Office files (docx/pptx). OFF by default — like the
    # figure path it adds a vision call per real content image, so it stays opt-in.
    # When on, images embedded in Word/PowerPoint (screenshots, photos, charts that
    # would otherwise be silently dropped) are vision-OCR'd and their text folded
    # into the page. Tiny decorative images (logos/icons/bullets, either edge under
    # the min-edge floor) are skipped without a call. DOCAIQ_DOCUMENTS_OFFICE_IMAGE_OCR=true.
    documents_office_image_ocr: bool = False
    # Cost cap: max embedded images vision-OCR'd per Office document.
    documents_office_image_max: int = 12
    # Universal parsing architecture — the structured Document Model (IR). OFF by
    # default; no runtime effect until a format's parser is switched to build the
    # IR (phased, eval-gated). See docs/UNIVERSAL_PARSING_ARCHITECTURE.md.
    doc_model: bool = False
    # G11 · multi-pass OCR voting. OFF by default — runs a 2nd vision pass on
    # low-confidence (G3) scanned pages and keeps the higher-quality transcript
    # (extra cost only on bad pages). DOCAIQ_DOCUMENTS_MULTIPASS_OCR=true.
    documents_multipass_ocr: bool = False
    # llm_audit_enabled · always logs gateway.call to llm_call_audit
    # with HASHES (not contents) of prompt/response. Cheap, safe,
    # required for any compliance-grade deployment.
    llm_audit_enabled: bool = True
    # llm_provider_allowlist · comma-separated. Empty = all providers
    # allowed. Set to (e.g.) 'anthropic,stub' to block cross-border
    # routes that violate residency policy.
    llm_provider_allowlist: str = ""
    # data_residency · tenant's declared region. Audit rows tag each
    # call with the provider's residency so a compliance officer can
    # answer 'did data X ever cross into Y jurisdiction?' from SQL.
    data_residency: str = "global"  # one of: EU / US / SG / IN / CN / global
    # Google Drive *public* read API key — used ONLY for enumerating files
    # in a publicly-shared Drive folder when a vendor pastes a folder URL.
    # Drive's public file-list endpoint requires either an API key or OAuth;
    # the API key path is read-only and doesn't need user consent. Get one
    # at https://console.cloud.google.com/apis/credentials in ~2 minutes,
    # restrict to the Drive API only. Leaving this empty disables folder
    # enumeration but single-file Drive links + Dropbox/zip still work.
    google_drive_api_key: str = ""

    # M46 · Documents System · Google Drive connector. When on (documents
    # product only), users can connect a Drive account, browse folders, and
    # sync files into their private workspace. `drive_backend` selects the
    # implementation: "stub" is a deterministic dev fake (no network, no creds
    # — used for local testing) and "google" is the real OAuth + Drive v3 path
    # (activates when google_client_id/secret are set). Mirrors the
    # embeddings/LLM pluggable-backend pattern.
    documents_drive_connector: bool = True
    drive_backend: Literal["stub", "google"] = "stub"
    # M46 · auto-mirror direct uploads to the user's docaiq_docs Drive folder
    # after processing, then purge the server copy (re-pullable on demand). Makes
    # Drive the store of record so server storage stays flat. No-op when Drive
    # isn't connected. Manual backfill is always available regardless.
    documents_drive_autobackup: bool = True

    # M46 · Documents System · universal-adaptive extraction. When on (documents
    # product only), the fact extractor ALWAYS uses the universal schema —
    # type-agnostic, so a mis-classification never routes a doc to the wrong
    # curated schema. `documents_extract_verify` adds a second self-critique LLM
    # pass that fills in anything the first pass missed (completeness). Audit
    # keeps its curated per-type dispatch (these flags are documents-gated).
    documents_universal_extractor: bool = True
    documents_extract_verify: bool = True
    # M46 · Documents System · type-agnostic agentic chat. When on, documents
    # chat routes through the ReAct Document Agent (multi-step tool use:
    # search_chunks / get_extracted_field / cross_doc_search / validate_id …)
    # instead of the single-shot full-doc/RAG steps — sharper, multi-part,
    # cited answers on any doc. The cheap zero-LLM steps + artifact fallback
    # still apply. Documents-gated; audit chat is unchanged.
    documents_agentic_chat: bool = True
    # Hybrid chat · when on, the tool-using workspace agent also handles the general
    # long tail — any question the deterministic fast-paths (counts, name/type/category
    # lists, identity, money) DIDN'T answer routes to the agent (which reasons over the
    # shared tool set: search_across, find_documents, find_by_person, get_field, …)
    # instead of straight to RAG. Off → the pre-hybrid behaviour (agent only for
    # actions; content questions go to RAG). Agent errors fall back to RAG either way.
    documents_agent_fallback: bool = True
    # M46 · §3 · run the Critic (self-correction) on documents chat answers from
    # the live pipeline (full_doc_ctx / rag_retrieval). One cheap critic call per
    # answer; refines only when the critic flags the draft. Documents-gated;
    # audit chat is unchanged. critic_max_refines bounds the refine loop.
    documents_critic_enabled: bool = True
    critic_max_refines: int = 1
    # M46 · §5 · auto-sync each connected user's docaiq_docs Drive folder on a
    # schedule (worker cron, every 15 min) so dropped files ingest without a
    # manual sync. Documents product only. Dedup by sha256 → re-syncs are cheap.
    documents_drive_autosync: bool = True
    # M46 · B7 · client-side encryption of files we store in the user's Drive.
    # When ON, files are encrypted (per-user, server-escrowed key) before upload
    # so Google can't read them at rest. Toggle freely — already-encrypted files
    # always decrypt on the way back (the blob is self-describing).
    documents_drive_encryption: bool = False
    # M46 · §5 · where document retrieval reads from. "postgres" (authoritative,
    # default) or "drive" (EXPERIMENTAL — read from the user's encrypted
    # workspace.sqlite in their Drive; Postgres stays the source of truth). The
    # workspace EXPORT is always available; this only switches the READ path.
    documents_storage_mode: str = "postgres"
    # M46 · §5 · nightly auto-sync each user's workspace to their Drive (writes to
    # real user Drives → opt-in, default off).
    documents_workspace_autosync: bool = False
    # M46 · §compliance · data retention. When > 0, a daily worker job moves
    # each connected user's server-stored originals OLDER than N days into their
    # Drive (then purges the server blob — re-pullable), minimizing what we hold
    # at rest. 0 = off (originals kept on the server until manual backup).
    documents_retention_purge_days: int = 0
    # LLM call ledger retention. The `llm_calls` + `llm_call_audit` tables grow
    # ~1.5M rows/mo at 1000 users × 50 docs. When > 0, a daily job deletes rows
    # older than this many days. Default 0 (OFF) so an existing ledger is never
    # purged on deploy — set DOCAIQ_LLM_CALLS_RETENTION_DAYS=90 to enable.
    llm_calls_retention_days: int = 0
    # M47 · retrieval_metrics retention. When > 0, a lightweight inline cleanup
    # (probabilistic, ~1:100 calls) deletes rows older than this many days.
    # Default 90 keeps a rolling quarter of observability data; set to 0 to
    # disable — DOCAIQ_RETRIEVAL_METRICS_RETENTION_DAYS.
    retrieval_metrics_retention_days: int = 90
    # M46 · §2 · self-learning Phase 2 (centroid distillation). A weakly-typed
    # doc whose summary-embedding is within this cosine similarity of a learned
    # type's centroid is auto-assigned that type WITHOUT an LLM call.
    centroid_match_threshold: float = 0.86
    # Canonicalize free-form doc-type slugs (from the classifier's out-of-enum
    # fallback + the type-reconciler's LLM path) to the nearest canonical DOC_TYPES
    # entry via a synonym/alias map — so 'medical_lab_report' / 'laboratory_test_report'
    # both converge on 'lab_report' instead of fragmenting. Default OFF = today's
    # behavior byte-for-byte; genuinely-new types still stay open-vocabulary (a slug
    # with no alias is left untouched).
    type_canonicalize: bool = False
    # Session revocation. When on, each session JWT carries a `tv` (token_version)
    # claim and every authenticated request re-checks it against the user's current
    # token_version (+ is_frozen) via a cheap indexed lookup — so logout-all, a
    # password change, or an account freeze instantly invalidate live sessions.
    # Default OFF = today's pure-JWT path (no per-request DB read). Tokens without a
    # `tv` claim (issued before this) are always accepted, so enabling is zero-impact
    # until a counter is actually bumped.
    session_revocation: bool = False
    # Generic entity+type resolver in the cross-doc chat. When on, a query naming any
    # graph entity (person / org / vendor / place / product) + optional doc-type — e.g.
    # "Rajesh Goda's national id", "invoices from Acme", "documents about Singapore" —
    # is answered by resolving the name against the entities graph, narrowing to that
    # entity's documents, filtering by type, and returning the values with their source
    # doc. Deterministic (no LLM), owner-scoped. Default OFF → the chain is unchanged.
    entity_type_resolver: bool = False
    # M46 · cap how many documents a single cross-doc (workspace) chat retrieves
    # over, so a "summarize everything" across a huge library doesn't load every
    # row + scan every chunk. Broad overview questions get a deterministic
    # aggregate instead; content questions retrieve over the most-recent N.
    documents_workspace_max_docs: int = 800
    # M53 · LLM spend guards (app/cost_guard.py). 0 = OFF. Enable for public/
    # testing waves to cap total + per-user LLM spend.
    documents_daily_llm_cap: int = 0          # total LLM calls / UTC day (budget kill-switch)
    documents_user_hourly_llm_cap: int = 0    # per signed-in user / hour (base tier)
    documents_enterprise_hourly_llm_cap: int = 100  # per hour for enterprise-plan users (higher)
    # M46 · input/output guardrail on documents chat: deterministic prompt-
    # injection screen on the question + a grounding critique on the answer
    # (regenerate once, then caveat if still ungrounded).
    documents_chat_guardrail: bool = True
    # R1 · calibrated chat abstention. Refuse with INSUFFICIENT_EVIDENCE rather
    # than answer from thin evidence. `min_top_score` stays None (score floor
    # OFF) until the R4 faithfulness golden set calibrates it; `on_ungrounded`
    # is the opt-in strict mode (refuse vs soft caveat after a failed regenerate).
    chat_abstain_enabled: bool = True
    # #3 · assemble the cross-doc answer RULES from a base + only the fragments the
    # question's shape needs (vs the fixed all-rules block). Off = legacy behaviour.
    # A/B via DOCAIQ_ANSWER_FRAGMENTS_ENABLED. See services/answer_fragments.py.
    answer_fragments_enabled: bool = False
    # #4 · typed answer contract — ask the answer LLM for a validated {answer,
    # answer_found, format, caveats} object (rendered back to text) instead of free
    # text. Off = legacy free-text. A/B via DOCAIQ_TYPED_ANSWER_ENABLED. The typed
    # path routes through doc_chat.llm_one_shot, so it uses the tenant's tier-1 model
    # (same as the free-text path) — no separate model knob needed.
    typed_answer_enabled: bool = False
    chat_abstain_min_hits: int = 1
    chat_abstain_min_top_score: float | None = None
    # When the grounding critic still flags an answer as unsupported after its
    # refine pass, REFUSE (insufficient evidence) instead of returning it with a
    # soft caveat. ON by default — for a document/compliance product a refusal is
    # safer than a shaky answer. Independent of retrieval score scale (the RRF
    # floor can't discriminate). Set false to revert to the caveat behavior.
    chat_abstain_on_ungrounded: bool = True
    # R3 · per-sentence citations. Attribute each answer sentence to its source
    # and drop citations that back no sentence. `drop_unsupported_sentences`
    # (strict) removes ungrounded sentences from the answer — default OFF so we
    # never silently edit an answer; default keeps them, just cites precisely.
    chat_sentence_citations: bool = True
    chat_sentence_support_min: float = 0.5
    chat_drop_unsupported_sentences: bool = False
    # R2 · chain-of-verification (per-claim LLM faithfulness). One extra LLM call
    # per answer, so OPT-IN (default off). `drop` removes unsupported claims from
    # the answer (strict); default just flags them.
    chat_claim_verification: bool = False
    chat_claim_drop_unsupported: bool = False
    # R5 · query routing + multi-hop decomposition + corrective-RAG (CRAG) for
    # cross-document chat. OPT-IN (default off): adds an LLM rewrite on weak
    # retrieval and a decomposition call on multi-hop questions, so it trades a
    # little cost/latency for better cross-doc recall. When off, the workspace
    # RAG path is byte-identical to before.
    chat_query_routing: bool = False
    chat_crag_max_hops: int = 2
    chat_multihop_max_subqs: int = 4

    # OpenRouter required headers — the platform asks for these to identify
    # apps using free tiers. Defaults to our github / docaiq.io brand.
    openrouter_referer: str = "https://docaiq.io"
    openrouter_app_title: str = "DocAIQuest"

    # Per-call timeout. LLMs occasionally hang; the cascade needs to fail
    # over to the next tier rather than dragging the whole request.
    llm_request_timeout: int = 60

    @model_validator(mode="after")
    def _privacy_first_documents_defaults(self) -> "Settings":
        """M46 · §PII · the documents product holds personal user content, so it
        is privacy-first BY DEFAULT: PII is tokenized at rest, redacted before any
        third-party LLM call, and NOTHING is contributed to the cross-tenant
        global knowledge pool. An operator can still override any of these via the
        explicit env var (we only set the default when the field wasn't given)."""
        if self.product != "documents":
            return self
        given = self.model_fields_set
        if "pii_protect_at_rest" not in given:
            self.pii_protect_at_rest = True
        if "pii_redact_before_llm" not in given:
            self.pii_redact_before_llm = True
        if "contribute_learning" not in given:
            self.contribute_learning = False
        return self

    @model_validator(mode="after")
    def _refuse_dev_secrets_in_production(self) -> "Settings":
        if self.environment != "production":
            return self
        weak = (
            len(self.jwt_secret) < 32
            or self.jwt_secret.startswith(("dev-", "ci-", "test-"))
            or self.jwt_secret == "dev-only-do-not-use-in-prod-please-rotate-me-please"
            or "rotate" in self.jwt_secret.lower()
            or "never-use" in self.jwt_secret.lower()
        )
        if weak:
            raise ValueError(
                "DOCAIQ_JWT_SECRET is missing, too short, or a dev placeholder while "
                "DOCAIQ_ENVIRONMENT=production. Refusing to boot — a predictable secret "
                "lets anyone forge this tenant's session cookies. Generate one with: "
                "python3 -c 'import secrets; print(secrets.token_urlsafe(48))'"
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
