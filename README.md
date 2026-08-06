# DocAIQuest

**Open-source document intelligence engine.** Ingest, parse, chunk, embed,
retrieve, and extract structured data from your documents — with AI-powered
chat. Self-hosted. You bring your own LLM keys. Privacy-native by default.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

> **You need your own LLM provider key.** DocAIQuest OSS does not ship with
> managed LLM access. Set at least one of `DASHSCOPE_API_KEY` (recommended),
> `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `DEEPSEEK_API_KEY`,
> or `OPENROUTER_API_KEY` in your `.env` file before starting. Without a key,
> parsing and chunking work — but extraction and chat won't.

## What it does

DocAIQuest is a complete document intelligence platform covering the full chain:
**parse → chunk → embed → retrieve → extract → graph → chat → API → SDK.**

### File Type Support

| Format | Parser | Table extraction | Vision OCR | IR blocks |
|--------|--------|:---:|:---:|:---:|
| **PDF (text)** | Docling → PyMuPDF structured → flat | ✅ Camelot + pdfplumber | — | ✅ |
| **PDF (scanned)** | Vision OCR cascade (Gemini→Qwen-VL→Claude) | — | ✅ Per-page | ✅ Via markdown→IR |
| **DOCX** | python-docx | ✅ | Embedded images | ✅ |
| **XLSX** | openpyxl (each sheet = page) | ✅ Native | — | ✅ |
| **CSV / TSV** | chardet + stdlib csv → Markdown table | ✅ Rendered as GFM table | — | — |
| **PPTX** | python-pptx (each slide = page) | ✅ | Embedded images | ✅ |
| **EML** | email stdlib (headers + body) | — | — | — |
| **Images** | HEIC/AVIF→JPEG transcode, EXIF autorotate | — | ✅ Gemini→Qwen-VL→Claude | ✅ |
| **HTML** | BeautifulSoup → text | — | — | — |
| **Plain text** | TXT, MD, Markdown, LOG | — | — | — |
| **Legacy Office** | LibreOffice → PDF pipeline (doc/xls/odt/rtf) | ✅ Same as PDF | If scanned | If Docling on |

### Parsing Quality

| Capability | Detail |
|-----------|--------|
| Layout-aware parsing | IBM Docling — multi-column reading order, heading/paragraph/list/table/figure blocks with real bboxes |
| Table extraction | Camelot (lattice + stream) → pdfplumber (lattice + text-strategy for borderless tables), ≤10 tables/page |
| OCR cascade | RapidOCR (ONNX, MIT, PP-OCRv6) + Tesseract fallback + Vision cascade (Gemini→Qwen-VL→Claude) |
| IR document model | Typed blocks: HEADING, PARAGRAPH, LIST_ITEM, KEY_VALUE, TABLE, FIGURE — each with page, bbox, confidence |
| Bbox geometry | Word-level + block-level positions stored in `line_map` + `block_map` JSONB; clickable citations in chat |
| Résumé handling | Word-level two-column reading-order reconstruction, gated on CV vocabulary + section heading count |
| MRZ protection | Passport/ID machine-readable zones never split across chunks |
| Multilingual | BGE-M3 (1024d, 100+ languages) + RapidOCR (90+ languages) |
| Async processing | Arq worker — documents processed in background with per-doc status tracking (queued → parsing → chunking → embedding → extracting → done) |
| Incremental updates | Re-uploading a doc only reprocesses changed chunks (SHA-256 hash dedup at the chunk level) |
| Document strategist | Auto-profiles every doc at upload (pages, text density, MIME) → routes to best pipeline (chunking strategy, extraction timeout, priority) |

### Chunking

| Capability | Detail |
|-----------|--------|
| Strategy | Block-aware: packs whole paragraphs, never splits KEY_VALUE/HEADING blocks |
| Table chunks | Separate `kind='table'` chunks, split on row boundaries with header repeated across continuation chunks |
| Overlap | 150-char paragraph-level overlap between windows |
| Sentence-aware | Optional snap-to-sentence-boundary on window cuts |
| Deduplication | 3-gram shingle Jaccard ≥ 0.9 near-duplicate detection |
| Contextual retrieval | Anthropic-style ~50–100 token LLM context prefix per chunk — embedding input enriched, recall +35–49% |
| Max chunks | 400 per page |

### Embeddings

| Backend | Model | Dimension | Notes |
|---------|-------|:---:|-------|
| **Local v1** | all-MiniLM-L6-v2 | 384d | Default, CPU, ~90MB, Apache 2.0 |
| **Local v2** | BGE-M3 (BAAI) | 1024d | Multilingual, CPU, ~2.2GB, MIT |
| **OpenAI** | text-embedding-3-small | configurable | Remote API |
| **DashScope** | text-embedding-v4 | configurable | Remote API |
| **Gemini** | gemini-embedding-001 | configurable | Remote API |
| **Hash** | Deterministic feature-hash | 384d | Offline / CI mode, no model needed |

- Dual embedding columns (`embedding` v1 384d + `embedding_v2` 1024d) with reversible migration
- `embed_signature()` stamps `backend:model:dim` for provenance tracking
- L2-normalized; HNSW index for cosine search

### Retrieval

| Stage | Method | Detail |
|:---:|--------|-------|
| 1 | **BM25** | PostgreSQL `tsvector` full-text search on chunk text |
| 2 | **Vector** | pgvector cosine similarity search, HNSW-indexed |
| 3 | **RRF fusion** | Reciprocal Rank Fusion — weighted combination of BM25 + vector ranks |
| 4 | **Rerank** | Cross-encoder (ms-marco-MiniLM-L-6-v2, CPU ~1.3s/query; BGE-Reranker-v2-m3 GPU optional) |
| 5 | **Context boost** | Contextual retrieval prefixes improve recall 35–49% |
| — | **Doc-name rescue** | Exact filename match boosts relevant documents |
| — | **Per-doc scoping** | Single-document + cross-document workspace modes |

### Extraction (structured data from documents)

| Capability | Detail |
|-----------|--------|
| Schema-driven | Zero-shot — define a JSON schema, get structured fields back with values + confidence + bbox citations |
| Built-in schemas | 12 types: invoice, receipt, bank_statement, agreement, insurance_certificate, certificate, business_profile, revenue_invoice, customer_payment, id_document, resume, universal |
| Schema library | HITL-approved schemas + AI Schema Architect (cloud) + learned field vocabulary + crystallized per-type schemas |
| Field confidence | Per-field + per-document confidence scoring (G4 scorer) |
| Geometry | `field_bboxes` — every extracted field pinned to its page location for clickable citations |
| Row extraction | `line_items[]`, `top_transactions[]` — full table-row extraction from invoices, statements, receipts |
| Type-mismatch retry | Auto-fallback to universal schema when curated schema confidence < 0.4 |
| Multi-pass verify | Cloud-only: up to 3 rounds of missing-row detection with row reconciliation |
| Résumé post-processing | Key normalization + deterministic marks-rescue from source text |
| Post-extraction categorization | Transaction rows auto-categorized (Travel, SaaS, Utilities…) |

### Entity Graph

| Capability | Detail |
|-----------|--------|
| Entity extraction | Regex (money, date, standards, control IDs) + optional LLM NER (person, org, location) |
| Graph bootstrap | Per-schema handlers emit typed nodes (person, org, money, date, location, identifier, transaction…) |
| Relation extraction | 30+ directed edge types: `signed_by`, `paid_to`, `has_transaction`, `effective_on`, `governed_by`, `settles_invoice`, `born_on`, `issued_by`… |
| Cross-doc resolution | Union-find clustering: exact match, token-subset, Jaccard > 0.5, substring, Levenshtein ≤ 3 |
| Durable identities | `entity_identity` table — stable canonical person/org nodes surviving individual doc deletion |
| Canonical aliases | `entity_canonical` — per-tenant name variants + observed counts |
| Graph audit log | Append-only `graph_runs` — every pass recorded, rollback-safe |
| Reconciliation | Cross-document payment/revenue matching for receipts + bank statements |

### Chat

| Capability | Detail |
|-----------|--------|
| Single-doc RAG | Source citations with bbox jump-to-location, thinking disclosure, structured answers |
| Cross-doc workspace | Multi-document reasoning across all user's documents |
| Groups (Knowledge Bases) | Group documents into shared folders — chat across a group, add collaborators, sync from Drive folders |
| Deterministic handlers | SQL-only fast paths: identity lookup, watchlist, document overviews, entity aggregation |
| Agent fallback | Cloud-only: ReAct loop with 9 tools (search_chunks, get_extracted_field, search_entities, cross_doc_search…) |
| History-aware | Multi-turn with follow-up query rewriting (`_contextualize_query`) |
| Streaming | Token-by-token streaming responses |
| Source citations | `【JSON:field】` markers → clickable bbox navigation in document viewer |
| Analytics cards | Inline stat cards + bar charts from tabular answers |
| Guardrails | Lab reference-range caution, chat quality guard, PII detokenization |

### API & Developer Surface

| Surface | Detail |
|--------|--------|
| REST API | Full CRUD: documents, extraction, chat, entities, graph, schema library, groups, connectors |
| MCP server | `POST /api/mcp` — Streamable-HTTP JSON-RPC. Tools: `ask_documents`, `list_documents`, `get_watchlist`. Works with ChatGPT + Claude. |
| Python SDK | `sdks/python/` — typed client for extraction + chat + document management |
| TypeScript SDK | `sdks/typescript/` — typed client for extraction + chat + document management |
| Self-serve API keys | `POST/GET/DELETE /api/keys` — owner-scoped keys (`dq_live_…`), mint/revoke in UI |
| External extraction API | `POST /api/extraction/extract` (X-API-Key) — cross-app extraction reuse |
| Swagger | `/api/docs` — interactive OpenAPI docs |
| Partner keys | Cloud-only — cross-tenant API keys for partners |

### Privacy & Security

| Capability | Detail |
|-----------|--------|
| PII redaction | At LLM boundary — mask sensitive IDs/contacts pre-call, detokenize post-call |
| PII at rest | Fernet-encrypted vault (`pii_vault` table) — real values encrypted, chunks carry `[TOKEN]` placeholders |
| Per-user isolation | `ContextVar` middleware + repo-layer filtering — users never see each other's documents |
| Multi-tenant | Tenant-level partition on all tables |
| Password hashing | argon2-cffi |
| JWT auth | PyJWT with configurable secret + expiry |
| Google Drive | `drive.file` scope only — no `drive.readonly`, avoids Google CASA audit |
| Consent records | GDPR consent audit trail (`consent_records` table) |
| Data residency | Per-call provider tracking for compliance reporting |

### UI

| Capability | Detail |
|-----------|--------|
| Framework | React 18 + Vite, vanilla CSS (no framework dependency) |
| Document viewer | PDF.js with bbox highlights, 3 view modes: Rendered, Blocks (IR), Markdown (editable) |
| Workbench layout | Resizable 3-pane IDE-style: doc list / viewer / chat |
| Landing page | Marketing site with rotating hero, feature comparison, live demo, CTA |
| Mobile responsive | Full mobile layout with tab bar, stacked panes |
| Dark theme | System-aware, CSS custom properties |
| Persona switching | Multi-role users toggle between views (owner/admin/reviewer/vendor) |
| Feedback | In-app feedback with screenshots, chat 👍/👎 |
| Admin console | Cloud-only — standalone superadmin UI (users, API clients, feature flags, reprocess, LLM analytics, QA tracker) |

### Database

| Store | Engine | Purpose |
|------|--------|---------|
| Primary DB | PostgreSQL 15+ | Documents, chunks, entities, users, schema library, chat history, feedback |
| Vector DB | pgvector (same PostgreSQL) | HNSW cosine search on `embedding` (384d) + `embedding_v2` (1024d) columns. **One database for everything** — no separate vector DB to run, backup, or tune. |
| Cache / Queue | Redis | Arq task queue, session cache, rate limiting |
| Object store | MinIO (S3-compatible) | Original file blobs, export artifacts |

Compare: most RAG stacks require MySQL + a separate vector DB (ChromaDB/Qdrant/Weaviate/Pinecone) + object storage. DocAIQuest collapses relational + vector into one PostgreSQL instance — three data stores become two.

### Deployment

| Mode | Detail |
|------|--------|
| Full stack | `docker compose up` — 6 services: postgres, redis, minio, backend, worker, frontend |
| Slim build | `docker-compose.min.yml` — no GPU models, hash embeddings, hash/cache-only reranker |
| Min RAM | ~4 GB (MiniLM only) / ~8 GB (BGE-M3 + all models) |
| BYO LLM keys | Required for OSS — set provider keys in `.env` |
| DB migrations | Alembic — `alembic upgrade head` runs automatically on backend boot |

## Quick start

**Before you run:** open `.env` and set the two required values:

| Variable | What to set |
|----------|-------------|
| `DOCAIQ_JWT_SECRET` | Any random string (e.g. `openssl rand -hex 32`) |
| LLM provider key | At least one of: `DASHSCOPE_API_KEY` (recommended), `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `DEEPSEEK_API_KEY`, or `OPENROUTER_API_KEY` |

Without a provider key, parsing and chunking work — but extraction and chat won't.

```bash
git clone https://github.com/rbgoda/docaiquest.git && cd docaiquest
cp .env.example .env
# NOW EDIT .env — set DOCAIQ_JWT_SECRET + at least one LLM provider key
make up
# → http://localhost:8085
# → Sign up (dev login auto-verifies), upload a PDF, chat + extract
make down   # stop
```

**Optional:** set `GOOGLE_CLIENT_ID` + `GOOGLE_CLIENT_SECRET` to store originals in
users' own Google Drive. Without it, file upload still works — files go to MinIO instead.

## Architecture

```
┌──────────────┐    ┌──────────────┐    ┌──────────────┐
│   Frontend   │    │   Backend    │    │    Worker    │
│  Vite + React│───▶│   FastAPI    │───▶│     Arq      │
│   :8085      │    │   :8093      │    │  (Redis Q)   │
└──────────────┘    └──────┬───────┘    └──────┬───────┘
                           │                   │
                    ┌──────┴───────┐    ┌──────┴───────┐
                    │  PostgreSQL  │    │    MinIO      │
                    │  + pgvector  │    │  (S3 store)   │
                    └──────────────┘    └──────────────┘
```

```
Document → Parse → Chunk → Embed → Store ─┐
                                           ├→ Retrieve → RAG Chat
Document → Entities → Resolve → Graph ────┘

Document → Classify → Extract (schema) → Structured Fields
```

The pipeline: `ingestion` → `chunking` → `embeddings` → `entities` → `retrieval`.
Full deep dive: [`docs/ARCHITECTURE_DEEP_DIVE.md`](docs/ARCHITECTURE_DEEP_DIVE.md).
Complete pipeline map: [`docs/PIPELINE_MAP.md`](docs/PIPELINE_MAP.md).

## Documentation

| Doc | What |
|-----|------|
| [`CLAUDE.md`](CLAUDE.md) | Architecture overview, layout, ops, deploy |
| [`docs/ARCHITECTURE_DEEP_DIVE.md`](docs/ARCHITECTURE_DEEP_DIVE.md) | End-to-end system walkthrough |
| [`docs/PIPELINE_MAP.md`](docs/PIPELINE_MAP.md) | Complete pipeline map — every parser, DB table, data flow |
| [`docs/PII_AND_PRIVACY.md`](docs/PII_AND_PRIVACY.md) | PII handling, redaction, privacy settings |
| [`docs/SDK_AND_API_DESIGN.md`](docs/SDK_AND_API_DESIGN.md) | External API design + SDKs |
| [`docs/API_QUICKSTART.md`](docs/API_QUICKSTART.md) | External API quickstart |
| [`docs/UNIVERSAL_PARSING_ARCHITECTURE.md`](docs/UNIVERSAL_PARSING_ARCHITECTURE.md) | Parsing architecture (Document Model IR) |
| [`docs/COMMERCIAL_PACKAGING.md`](docs/COMMERCIAL_PACKAGING.md) | Commercial licensing + packaging |

## OSS vs Cloud

This repo is the open-source **engine** (`DOCAIQ_LICENSE_MODE=oss`):

| Tier | What you get |
|------|-------------|
| **OSS** (free, this repo) | Full document pipeline with your own LLM keys · 11 file formats · layout-aware parsing + table extraction · block-aware chunking · dual embeddings (MiniLM + BGE-M3) · hybrid retrieval + cross-encoder rerank · schema-driven extraction (12 built-in schemas) · entity graph + cross-doc resolution · single-doc RAG chat with bbox citations · REST API + MCP server + Python/TS SDKs · PII redaction + encryption · multi-tenant + per-user isolation |
| **Cloud** (from $49/mo) | Everything in OSS, plus: agentic chat (tool-using ReAct loop) · multi-pass extraction with row verification · cross-document workspace chat · AI Schema Architect · watchlist + assistant (renewals, reminders, .ics) · reflexion learning (improves from feedback) · Drive auto-sync · schema autopilot · LLM cost analytics · managed LLM access · priority support |

## Stack

**Backend:** Python 3.12 · FastAPI · SQLAlchemy 2 + Alembic · PostgreSQL + pgvector ·
Redis · Arq · MinIO (S3) · PyMuPDF · Docling · RapidOCR · BGE-M3 · sentence-transformers

**Frontend:** React 18 · Vite · vanilla CSS (no framework)

**Infra:** Docker Compose (6 services — postgres, redis, minio, backend, worker, frontend)

## License

DocAIQuest is [MIT](LICENSE) licensed. See [NOTICE](NOTICE) for third-party attributions,
including model weights and bundled libraries.

---

Powered by DocAIQuest — [github.com/rbgoda/docaiquest](https://github.com/rbgoda/docaiquest)
