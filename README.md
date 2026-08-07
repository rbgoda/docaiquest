# DocAIQuest

**Self-hosted Document Intelligence & GraphRAG Engine.** Documents → Data → Intel.
Upload any document, extract structured fields, build a knowledge graph, and
chat with your data — all through a browser. Privacy-native. BYO LLM keys.
MIT licensed.

> **Document parsing · OCR · chunking · embeddings · hybrid RAG (BM25 + vector) ·
> knowledge graph · entity resolution · structured extraction · agentic chat ·
> MCP server · Python SDK · TypeScript SDK · Docker self-hosted**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org/)
[![Docker](https://img.shields.io/badge/Docker-self--hosted-2496ED.svg)](https://docs.docker.com/compose/)
[![GitHub Discussions](https://img.shields.io/badge/Discussions-Q%26A-important.svg)](https://github.com/rbgoda/docaiquest/discussions)

> **You need your own LLM provider key.** DocAIQuest OSS does not ship with
> managed LLM access. Set at least one of `DASHSCOPE_API_KEY` (recommended),
> `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `DEEPSEEK_API_KEY`,
> or `OPENROUTER_API_KEY` in your `.env` file before starting. Without a key,
> parsing and chunking work — but extraction and chat won't.

## Screenshots

<p align="center">
  <img src="docs/screenshots/02-doc-list.png" width="48%" alt="Document list with upload zone" />
  <img src="docs/screenshots/03-chat-with-doc.png" width="48%" alt="Chat with document preview and citations" />
  <img src="docs/screenshots/04-sidebar-collapsed.png" width="48%" alt="Collapsible sidebar — more room for chat" />
  <img src="docs/screenshots/05-preview-collapsed.png" width="48%" alt="Collapsible document preview — maximize chat area" />
</p>

## Capabilities

DocAIQuest is a **self-hosted web console** — upload documents and chat with
them. Your own LLM keys, your own server, your data never leaves.

> **🟢 = DocAIQuest OSS (this repo)** &nbsp;&nbsp; **☁️ = [DocAIQ Cloud](https://docaiq.jicama.tech)** (hosted premium)
> — same engine, cloud adds agentic AI, multi-pass extraction, watchlist, and managed billing.

### Document Parsing

| Capability | OSS | Cloud |
|-----------|:---:|:-----:|
| PDF (text) — PyMuPDF + pdfplumber with layout preservation | 🟢 | ☁️ |
| PDF (scanned/OCR) — RapidOCR + vision cascade (Gemini → Qwen-VL → Claude) | 🟢 | ☁️ |
| DOCX / XLSX / PPTX — native parsers + LibreOffice fallback | 🟢 | ☁️ |
| CSV / TSV — native CSV reader, quote/newline/delimiter-aware, structured tables | 🟢 | ☁️ |
| Images (PNG, JPG, HEIC) — vision model OCR with multi-pass quality scoring | 🟢 | ☁️ |
| HTML — native parser preserving structure | 🟢 | ☁️ |
| EML (email) — native parser extracting headers, body, attachments | 🟢 | ☁️ |
| TXT / Markdown — native with encoding detection | 🟢 | ☁️ |
| Legacy Office (.doc, .xls, ODT, RTF) — LibreOffice conversion fallback | 🟢 | ☁️ |
| Multi-column PDF layout — word-level column reconstruction | 🟢 | ☁️ |

### Chunking & Embedding

| Capability | OSS | Cloud |
|-----------|:---:|:-----:|
| Block-aware chunking with configurable overlap windows, NFKC normalization | 🟢 | ☁️ |
| Semantic chunking — document-model-aware section boundary detection | 🟢 | ☁️ |
| 5 embedding backends: local (MiniLM), DashScope (BGE-M3), OpenAI, Gemini, OpenRouter | 🟢 | ☁️ |
| BGE-Reranker-v2-m3 and ms-marco-MiniLM cross-encoders, lazy singleton, configurable | 🟢 | ☁️ |

### Retrieval

| Capability | OSS | Cloud |
|-----------|:---:|:-----:|
| Vector search — pgvector cosine similarity with configurable dimension | 🟢 | ☁️ |
| Keyword search — BM25 sparse retrieval with PostgreSQL native text search | 🟢 | ☁️ |
| Hybrid retrieval — BM25 + pgvector cosine + Reciprocal Rank Fusion (RRF) | 🟢 | ☁️ |
| Cross-encoder reranker applied post-retrieval for precision | 🟢 | ☁️ |
| Graph retrieval — cross-doc entity graph traversal + entity profile resolution | 🟢 | ☁️ |
| Citation & sourcing — per-sentence source citations with bbox page-jump links | 🟢 | ☁️ |
| Abstention — refuses to answer when evidence is insufficient, with confidence scoring | 🟢 | ☁️ |
| Faithfulness pipeline — critic agent + claim verifier + guardrail, all configurable | 🟢 | ☁️ |
| Reflexion learning — improves future answers from 👍/👎 feedback | — | ☁️ |

### Extraction & Structured Data

| Capability | OSS | Cloud |
|-----------|:---:|:-----:|
| Field extraction — dates, amounts, parties, line items, IDs from invoices, receipts, contracts | 🟢 | ☁️ |
| 123-type document taxonomy + curated schema library with HITL approval workflow | 🟢 | ☁️ |
| Schema Architect — AI reads a document and proposes the optimal extraction schema | — | ☁️ |
| Multi-pass verification with row-reconciliation loop for statement/invoice line items | — | ☁️ |
| Single-pass extraction with per-field confidence scoring and trust scoring | 🟢 | ☁️ |
| Bulk operations — re-extract across all documents; scoped reprocess via admin console | 🟢 | ☁️ |
| Export — structured JSON, Markdown, CSV; deterministic Markdown export for reproducibility | 🟢 | ☁️ |

### Chat & Query

| Capability | OSS | Cloud |
|-----------|:---:|:-----:|
| Single-document RAG chat with citations + deterministic fast-paths (counts, money, identity, dates) | 🟢 | ☁️ |
| Agentic chat (ReAct loop) — document agent with 9 tools: search, extract, entities, cross-doc, final answer | — | ☁️ |
| Cross-document workspace agent — tool-using reasoning across all user documents | — | ☁️ |
| Cross-document deterministic handlers — SQL-only path for counts, totals, identity, library overview (zero LLM cost) | 🟢 | ☁️ |
| Multi-turn conversations — contextual query rewriting with full history awareness | 🟢 | ☁️ |
| MCP server — Streamable HTTP JSON-RPC, connect ChatGPT, Claude, or Cursor to your documents | 🟢 | ☁️ |
| Watchlist / Assistant — renewals, expiries, due dates with .ics calendar reminders | — | ☁️ |

### Knowledge Graph

| Capability | OSS | Cloud |
|-----------|:---:|:-----:|
| Entity extraction — NER + fact extraction: persons, orgs, dates, monetary amounts, identifiers | 🟢 | ☁️ |
| Cross-doc entity resolution — union-find clustering, Levenshtein distance, Jaccard similarity | 🟢 | ☁️ |
| Entity profiles — per-entity aggregated view across all documents | 🟢 | ☁️ |
| Graph insights — dashboard analytics: entity relationships, document overlap, concentration metrics | 🟢 | ☁️ |
| GraphRAG retrieval — combining vector and entity graph traversal | 🟢 | ☁️ |
| Persistent postgres-backed graph nodes, survives restarts | 🟢 | ☁️ |

### Multimodal & Vision

| Capability | OSS | Cloud |
|-----------|:---:|:-----:|
| Image OCR — vision cascade: Gemini → Qwen-VL → Claude, with quality scoring | 🟢 | ☁️ |
| Table extraction — GFM table rendering with blockMap bounding-box overlays for in-page locate links | 🟢 | ☁️ |
| Figure extraction — configurable figure/embedded-image extraction from PDFs and Office docs | 🟢 | ☁️ |
| Office image OCR — embedded images in DOCX/PPTX extracted and OCR'd (configurable flag) | 🟢 | ☁️ |

### Privacy & Security

| Capability | OSS | Cloud |
|-----------|:---:|:-----:|
| PII redaction — sensitive identifiers and contact details masked before reaching external LLM providers | 🟢 | ☁️ |
| Encryption at rest — optional Drive encryption, files openable only via DocAIQuest | 🟢 | ☁️ |
| Data residency — all data (documents, embeddings, extracted fields) stays in your own postgres and MinIO volumes | 🟢 | ☁️ |
| No telemetry — zero outbound calls beyond the LLM providers you configure | 🟢 | ☁️ |
| Per-user isolation — tenant middleware + repository-layer filtering | 🟢 | ☁️ |
| API key scoping — owner-scoped API keys minted by users; partner keys via admin console | 🟢 | ☁️ |

### API & SDK

| Capability | OSS | Cloud |
|-----------|:---:|:-----:|
| REST API — full OpenAPI (Swagger) at `/api/docs`: upload, extract, chat, search, list, export | 🟢 | ☁️ |
| Unified v1 API — single `POST /api/v1` endpoint with action field: ask, extract, list_documents | 🟢 | ☁️ |
| Python SDK — `pip install docaiquest`, typed client with async support | 🟢 | ☁️ |
| TypeScript SDK — `npm install @docaiquest/sdk`, typed client for Node.js and browser | 🟢 | ☁️ |
| MCP server — `/api/mcp`, Streamable HTTP JSON-RPC, tools: ask_documents, list_documents, get_watchlist | 🟢 | ☁️ |
| Self-serve API keys — users mint and revoke their own keys from Settings → API Keys | 🟢 | ☁️ |
| Managed LLM access — pre-configured provider keys, metered billing through DocAIQ | — | ☁️ |

### Frontend & UX

| Capability | OSS | Cloud |
|-----------|:---:|:-----:|
| Document viewer — rendered view, blocks view with bounding boxes, raw Markdown with edit+reprocess | 🟢 | ☁️ |
| Chat panel — split-pane chat with thinking disclosure, source citations, inline stat cards and bar charts | 🟢 | ☁️ |
| Document dashboard — stats capsules (docs, pages, ready count, format), extraction coverage badges | 🟢 | ☁️ |
| Collapsible/resizable panels — sidebar, document preview, zoom controls | 🟢 | ☁️ |
| Full-text search across all documents with relevance ranking | 🟢 | ☁️ |
| Google Drive connector — OAuth-based Drive folder sync with auto-ingest and encrypted backup | — | ☁️ |
| Responsive design — mobile-responsive across all views | 🟢 | ☁️ |

### Operations & Admin

| Capability | OSS | Cloud |
|-----------|:---:|:-----:|
| Admin console — standalone superadmin UI: user management, API clients, reprocess | 🟢 | ☁️ |
| Background jobs — Arq worker: ingestion, embedding, extraction, graph bootstrap, retention purge | 🟢 | ☁️ |
| LLM cost guard — per-user hourly and daily caps; per-document cost tracking | 🟢 | ☁️ |
| LLM cost analytics — per-model spend, daily/monthly charts, top documents by cost | — | ☁️ |
| Retention policies — configurable document retention purge (re-pullable from Drive) | 🟢 | ☁️ |
| Feedback system — per-answer user feedback with screenshot capture and triage dashboard | 🟢 | ☁️ |
| Eval harness — 1,180-question QA bank runner with LLM judge, R4 stdlib metrics, Ragas integration | 🟢 | ☁️ |
| Drive auto-sync — Google Drive inbox monitoring, automatic re-ingest on changes | — | ☁️ |
| Schema autopilot — sweeps untyped docs, proposes schemas automatically | — | ☁️ |

### Deployment

| Capability | OSS | Cloud |
|-----------|:---:|:-----:|
| Local deploy — `docker compose up`, single command, all services | 🟢 | ☁️ |
| Stack — postgres (pgvector) + redis + minio + backend (FastAPI) + worker (Arq) + frontend (Vite/React) | 🟢 | ☁️ |
| Resource requirements — 4 GB RAM minimum, 8 GB recommended; ~10 GB disk | 🟢 | ☁️ |
| Air-gapped capable — hash embedding backend + local models, zero external calls | 🟢 | ☁️ |
| Configuration — single `.env` file, 100+ knobs, sensible defaults for all | 🟢 | ☁️ |
| Platform — Linux, macOS (Docker); ARM64 and AMD64 | 🟢 | ☁️ |

### Supported file formats

| Format | Upload? | Chat? | Extract? | OSS | Cloud |
|--------|:--:|:--:|:--:|:---:|:-----:|
| PDF (text + scanned) | ✅ | ✅ | ✅ | 🟢 | ☁️ |
| DOCX (Word) | ✅ | ✅ | ✅ | 🟢 | ☁️ |
| XLSX / CSV / TSV | ✅ | ✅ | ✅ | 🟢 | ☁️ |
| PPTX (PowerPoint) | ✅ | ✅ | ✅ | 🟢 | ☁️ |
| Images (PNG, JPG, HEIC) | ✅ | ✅ | ✅ | 🟢 | ☁️ |
| EML (email) | ✅ | ✅ | ✅ | 🟢 | ☁️ |
| HTML | ✅ | ✅ | ✅ | 🟢 | ☁️ |
| TXT / Markdown | ✅ | ✅ | ✅ | 🟢 | ☁️ |
| Legacy Office (DOC, XLS, ODT, RTF) | ✅ | ✅ | ✅ | 🟢 | ☁️ |
| Audio / Video | ❌ | ❌ | ❌ | — | — |

## Quick start

### Prerequisites

- **Docker** + Docker Compose v2
- **4 GB RAM** minimum / **8 GB** recommended (multilingual embeddings use more memory)
- **~10 GB** free disk space (Docker images + database + file storage)
- **An LLM provider key** — you bring your own (see below)

### 1. Deploy

```bash
git clone https://github.com/rbgoda/docaiquest.git && cd docaiquest
cp .env.example .env
```

Now edit `.env` — the two **required** settings:

```ini
DOCAIQ_JWT_SECRET=<any random string, e.g. output of `openssl rand -hex 32`>
DASHSCOPE_API_KEY=<your DashScope API key>
```

`DASHSCOPE_API_KEY` is recommended — one key covers both chat and embeddings.
Any of these also work: `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`,
`DEEPSEEK_API_KEY`, `OPENROUTER_API_KEY`.

```bash
make up
```

This builds and starts 6 services: PostgreSQL (with vector search), Redis,
file storage, the API backend, a background worker, and the web frontend.
On first boot, database tables are created automatically. Wait ~30 seconds
for all services to settle.

### 2. Verify

```bash
# All 6 services running:
docker compose -p docaiquest ps

# Backend health check:
curl http://localhost:8085/api/health
# → {"status":"ok","tenant":"default","environment":"local","license_mode":"oss"}
```

Open **http://localhost:8085** in your browser.

### 3. Open the web app

1. **Sign up** — create an account. In local dev mode, email verification is
   skipped (accounts auto-verify).
2. **Upload a document** — drag a file onto the upload area or click to browse.
   The document appears in the left panel and processing begins automatically.
3. **Wait for processing** — takes 10–60 seconds depending on document size.
   The document is parsed, chunked, and indexed for search.
4. **Chat with it** — click the document, type a question in the chat panel.
   "What is this document about?" or "Summarize the key points."
5. **View extracted data** — extracted fields (dates, amounts, parties) appear
   alongside the document preview.

### Stop

```bash
make down          # stop containers, keep data
make down-clean    # stop + delete all data (fresh start)
```

### Optional configuration

| Variable | What it does |
|----------|-------------|
| `GOOGLE_CLIENT_ID` + `GOOGLE_CLIENT_SECRET` | Store originals in users' own Google Drive instead of local storage |
| `DOCAIQ_EMBED_BACKEND` | `local` (default, CPU embeddings) or `dashscope` (API) or `hash` (offline, no model needed) |
| `DOCAIQ_EMBED_V2_ACTIVE` | Set to `true` for higher-quality multilingual embeddings (needs ~2.2 GB extra disk) |
| `DOCAIQ_RERANKER_ENABLED` | Set to `true` for smarter search ranking (improves answer quality, CPU-friendly) |
| `DOCAIQ_ENVIRONMENT` | `local` (default) or `production` (enables stricter security) |

See `.env.example` for every available setting.

### Troubleshooting

| Symptom | Likely fix |
|---------|-----------|
| **Chat returns nothing** | Check your LLM provider key is set and funded. Try `curl` to the provider directly. |
| **Documents stuck "processing"** | Check worker logs: `docker compose -p docaiquest logs worker \| tail -30`. Usually a missing API key or rate limit. |
| **502 Bad Gateway** | Backend still booting — wait 10s and refresh. If it persists: `docker compose -p docaiquest logs backend \| tail -20`. |
| **Port 8085 already in use** | Change `FRONTEND_PORT` in `.env` to a different port. |
| **Out of disk space** | `docker builder prune -f --keep-storage 30GB && docker image prune -f` to reclaim build cache. |
| **Fresh start (wipe everything)** | `make down-clean && make up` — deletes all containers, volumes, and data. |

## Architecture

```
┌──────────────┐    ┌──────────────┐    ┌──────────────┐
│   Frontend   │    │   Backend    │    │    Worker    │
│  React       │───▶│   Python     │───▶│  Background  │
│  web :8085   │    │   API :8001  │    │  processing  │
└──────────────┘    └──────┬───────┘    └──────┬───────┘
                           │                   │
                    ┌──────┴───────┐    ┌──────┴───────┐
                    │  PostgreSQL  │    │  File storage │
                    │  (+ vectors) │    │  (S3 compat)  │
                    └──────────────┘    └──────────────┘
```

**Pipeline:** Upload → Parse → Chunk → Embed → Store → Retrieve → Generate answer.

### Retrieval

Hybrid BM25 + cosine similarity (BGE-M3 multilingual embeddings when
`DOCAIQ_EMBED_V2_ACTIVE=true`) → BGE-Reranker-v2-m3 cross-encoder for
re-ranking when `DOCAIQ_RERANKER_ENABLED=true`. Results are fused with
Reciprocal Rank Fusion (RRF).

### Parsing

Each format dispatches to the best available parser:

| Format | Primary parser | Notes |
|--------|---------------|-------|
| PDF (text) | PyMuPDF + pdfplumber | Word-level bbox extraction; tables via pdfplumber |
| PDF (scanned) | OCR cascade | RapidOCR → external vision model if configured |
| DOCX / PPTX | python-docx / python-pptx | Embedded images OCR'd when `DOCAIQ_DOCUMENTS_OFFICE_IMAGE_OCR=true` |
| XLSX | openpyxl | Sheets → structured Markdown tables |
| CSV / TSV | stdlib csv | Delimiter-sniffing, quote-aware |
| EML | Python email | MIME-aware, attachments extracted |
| Images | OCR cascade | PNG, JPG, HEIC, AVIF supported |
| HTML | BeautifulSoup | Text extraction + table preservation |
| Legacy Office | LibreOffice (headless) | DOC, XLS, ODT, RTF → converted to modern format first |

All formats normalize into a single structured Document Model IR before
chunking and embedding, so retrieval quality is consistent regardless of
source format.

## Privacy

DocAIQuest is **privacy-native by default**. Sensitive data is redacted at
the LLM boundary — before any text leaves your server for an external AI
provider. After the LLM responds, the original values are restored in the
answer shown to you.

### What is redacted

| Category | Examples | Redacted by default? |
|----------|----------|:--:|
| Account numbers, IBANs | `288-900557`, `DE89 3704 0044 …` | ✅ |
| Government IDs | NRIC, SSN, passport numbers | ✅ |
| Phone numbers, emails | `+65 1234 5678`, `a@b.com` | ✅ |
| Street addresses | `123 Main St, #05-01` | ✅ |
| Person names | `John Smith` | ❌ (they're the search key) |

### Settings

| Flag | Default | Effect |
|------|---------|--------|
| `DOCAIQ_PII_REDACT_BEFORE_LLM` | `true` | Enable the redaction round-trip |
| `DOCAIQ_PII_PROTECT_AT_REST` | `true` | Encrypt stored PII in the DB |
| `DOCAIQ_PII_REDACT_PERSON_NAMES` | `false` | Also mask person names (costs search quality) |

When redaction is on, the LLM sees placeholders like `[ACCOUNT_1]`, `[EMAIL_1]`.
The owner sees the real values — only in their own session.

## Layout

```
├── backend/           FastAPI application (package: `app`)
│   ├── app/
│   │   ├── agents/    Extraction, OCR, chat agents
│   │   ├── routers/   API endpoints
│   │   ├── services/  Business logic (chat pipeline, workspace)
│   │   ├── llm/       LLM gateway, prompts, routing
│   │   ├── graph/     Entity resolution & knowledge graph
│   │   └── jobs/      Background cron jobs
│   └── migrations/    Alembic (auto-run on boot)
├── frontend-oss/      OSS web console (Vite + React SPA)
├── admin-ui/          Superadmin console (static HTML)
├── sdks/              Python + TypeScript API clients
└── docker-compose.yml
```

## Development

### Prerequisites

- Python 3.11+
- Node.js 22+
- PostgreSQL with pgvector extension
- Redis

### Stack

- **Backend:** FastAPI + SQLAlchemy + Alembic + PostgreSQL (pgvector)
- **Worker:** Arq (Redis-backed async task queue)
- **Frontend:** Vite + React (SPA)
- **Storage:** MinIO (S3-compatible, local dev) / AWS S3

### Conventions

- **Schema = Alembic** (`backend/migrations/`). Adding a table or column
  requires a new migration — never use `create_all()`.
- **Per-user isolation:** `current_owner_user_pk` ContextVar (set by
  `TenantMiddleware`) + repo-layer filtering. Never bypass it.
- **Email verification** (Resend) activates only when
  `DOCAIQ_RESEND_API_KEY` is set; else signups auto-verify.

### Running locally (without Docker)

```bash
cd backend
pip install -e .
alembic upgrade head
uvicorn app.main:app --reload --port 8001
```

```bash
cd frontend-oss
npm ci
npm run dev          # Vite dev server on :5173
```

### Testing

Tests use a throwaway PostgreSQL + pgvector instance — never point at a live
database.

```bash
cd backend
PGPASSWORD=postgres psql -h localhost -U postgres -c "CREATE DATABASE test_docaiquest;"
pytest -q
PGPASSWORD=postgres psql -h localhost -U postgres -c "DROP DATABASE test_docaiquest;"
```

Run `ruff check backend/app` before committing — should exit 0.

## API

When the stack is running, interactive docs are at
**http://localhost:8085/api/docs**.

Quick reference:

| Endpoint | Description |
|----------|------------|
| `POST /api/v1/ask` | Grounded answer over your documents, with citations |
| `GET /api/v1/documents` | List your documents |
| `POST /api/extraction/extract` | Structured fields from a file (stateless) |
| `POST /api/mcp` | MCP endpoint for AI assistants (Claude, ChatGPT, Cursor) |

Auth: create an owner-scoped API key in the web app (Settings → API keys),
send it as `X-API-Key: dq_live_…` or `Authorization: Bearer dq_live_…`.

### Connect to ChatGPT

You can expose your DocAIQuest instance to a ChatGPT Custom GPT via the
OpenAPI schema at `/api/mcp/openapi.json`:

1. In the web app, go to **Settings → API keys** and create a key.
2. In ChatGPT, create a new Custom GPT.
3. Under **Actions**, import `http://your-instance:8085/api/mcp/openapi.json`.
4. Set authentication to **API Key** → `X-API-Key` with your `dq_live_…` key.
5. The GPT can now call `ask_documents` and `list_documents` against your instance.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

DocAIQuest is [MIT](LICENSE) licensed. See [NOTICE](NOTICE) for third-party attributions,
including model weights and bundled libraries.

---

Powered by DocAIQuest — [github.com/rbgoda/docaiquest](https://github.com/rbgoda/docaiquest)
