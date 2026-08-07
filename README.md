# DocAIQuest

**Self-hosted Document Intelligence & GraphRAG Engine.** 
Documents → Data → Intel.

Upload any document, extract structured fields, build a knowledge graph, and
chat with your data — all through a browser. 
Privacy-native. 
BYO LLM keys.
MIT licensed.

> **Document parsing · OCR · chunking · embeddings · hybrid RAG (BM25 + vector) ·
> knowledge graph · entity resolution · structured extraction · cross-document chat ·
> Docker self-hosted**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org/)
[![Docker](https://img.shields.io/badge/Docker-self--hosted-2496ED.svg)](https://docs.docker.com/compose/)
[![GitHub Discussions](https://img.shields.io/badge/Discussions-Q%26A-important.svg)](https://github.com/rbgoda/docaiquest/discussions)

> **You need your own LLM provider key.** D
> ocAIQuest OSS does not ship with managed LLM access.
> Set at least one of `DASHSCOPE_API_KEY` (recommended),
> `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `DEEPSEEK_API_KEY`,
> or `OPENROUTER_API_KEY` in your `.env` file before starting.
> Without a key, parsing and chunking work — but extraction and chat won't.

## Screenshots

<p align="center">
  <img src="docs/screenshots/02-doc-list.png" width="48%" alt="Document list with upload zone" />
  <img src="docs/screenshots/03-chat-with-doc.png" width="48%" alt="Chat with document preview and citations" />
  <img src="docs/screenshots/04-sidebar-collapsed.png" width="48%" alt="Collapsible sidebar — more room for chat" />
  <img src="docs/screenshots/05-preview-collapsed.png" width="48%" alt="Collapsible document preview — maximize chat area" />
</p>

## Capabilities

DocAIQuest is a **self-hosted web console** — upload documents and chat with them. 
Your own LLM keys, your own server, your data never leaves.

**Pipeline:** Upload → Parse → Chunk → Embed → Store → Retrieve → Generate answer.

### Document Parsing

| Capability | Detail |
|-----------|------------|
| PDF (text) | PyMuPDF + pdfplumber with layout preservation |
| PDF (scanned/OCR) | RapidOCR engine + vision cascade (Gemini → Qwen-VL → Claude) |
| DOCX / XLSX / PPTX | Native parsers (python-docx, openpyxl, python-pptx) + LibreOffice fallback |
| CSV / TSV | Native CSV reader, quote/newline/delimiter-aware, rendered as structured tables |
| Images (PNG, JPG, HEIC) | Vision model OCR with multi-pass quality scoring |
| HTML | Native parser preserving structure |
| EML (email) | Native parser extracting headers, body, attachments |
| TXT / Markdown | Native with encoding detection |
| Legacy Office (.doc, .xls, ODT, RTF) | LibreOffice conversion fallback |
| Multi-column PDF layout | Word-level column reconstruction, content-gated to protect table-heavy docs |

### Chunking & Embedding

| Capability | Detail |
|-----------|------------|
| Chunking strategies | Block-aware chunking, semantic chunking, configurable overlap windows, NFKC normalization |
| Embedding backends | 5 backends: local (MiniLM-L6-v2 384d, CPU, free), DashScope (BGE-M3 1024d), OpenAI, Gemini, OpenRouter |
| Reranker | BGE-Reranker-v2-m3 and ms-marco-MiniLM cross-encoders, lazy singleton, configurable |
| Semantic chunking | Document-model-aware section boundary detection |

### Retrieval

| Capability | Detail |
|-----------|------------|
| Vector search | pgvector cosine similarity with configurable dimension |
| Keyword search | BM25 sparse retrieval with PostgreSQL native text search |
| Hybrid retrieval | BM25 + pgvector cosine + Reciprocal Rank Fusion (RRF) |
| Reranking | Cross-encoder reranker applied post-retrieval for precision |
| Graph retrieval | Cross-doc entity graph traversal + entity profile resolution |
| Citation & sourcing | Per-sentence source citations with bbox page-jump links |
| Abstention | Calibrated abstention — refuses to answer when evidence is insufficient, with confidence scoring |

### Extraction & Structured Data

| Capability | Detail |
|-----------|------------|
| Field extraction | Dates, amounts, parties, line items, IDs — from invoices, receipts, contracts, and more |
| Schema system | 123-type document taxonomy + curated schema library with HITL approval workflow |
| Confidence scoring | Per-field confidence with trust scoring |
| Bulk operations | Re-extract across all documents; scoped reprocess via admin console |
| Export formats | Structured JSON, Markdown, CSV; deterministic Markdown export for reproducibility |

### Chat & Query

| Capability | Detail |
|-----------|------------|
| Single-document chat | RAG with citations + deterministic fast-paths for counts, money, identity, dates |
| Cross-document chat | Deterministic SQL handlers + RAG across all user documents |
| Deterministic handlers | SQL-only path for accurate counts, money totals, identity lookups — zero LLM cost |
| Multi-turn conversations | Contextual query rewriting with full history awareness |
| MCP server | Streamable HTTP JSON-RPC — connect ChatGPT, Claude, or Cursor directly to your documents |

### Knowledge Graph

| Capability | Detail |
|-----------|------------|
| Entity extraction | NER + fact extraction: persons, orgs, dates, monetary amounts, identifiers |
| Cross-doc entity resolution | Union-find clustering, Levenshtein distance, Jaccard similarity, configurable thresholds |
| Entity profiles | Per-entity aggregated view across all documents |
| Graph insights | Dashboard analytics: entity relationships, document overlap, concentration metrics |
| Graph retrieval | GraphRAG-enabled retrieval combining vector and entity graph traversal |
| Durability | Persistent postgres-backed graph nodes, survives restarts |

### Multimodal & Vision

| Capability | Detail |
|-----------|------------|
| Image OCR | Vision cascade: Gemini → Qwen-VL → Claude, with quality scoring |
| Table extraction | GFM table rendering with blockMap bounding-box overlays for in-page locate links |
| Figure extraction | Configurable figure/embedded-image extraction from PDFs and Office docs |
| Office image OCR | Embedded images in DOCX/PPTX extracted and OCR'd (configurable flag) |

### Privacy & Security

| Capability | Detail |
|-----------|------------|
| Data residency | All data (documents, embeddings, extracted fields) stays in your own postgres and MinIO volumes |
| No telemetry | Zero outbound calls beyond the LLM providers you configure |
| Per-user isolation | Tenant middleware + repository-layer filtering — each user sees only their own documents |

### API & SDK

| Capability | Detail |
|-----------|------------|
| REST API | Full OpenAPI (Swagger) at `/api/docs` — upload, extract, chat, search, list, export |

### Frontend & UX

| Capability | Detail |
|-----------|------------|
| Document viewer | Rendered view with page navigation, blocks view with bounding boxes, raw Markdown with edit+reprocess |
| Chat panel | Split-pane chat with thinking disclosure, source citations, inline stat cards and bar charts |
| Document dashboard | Stats capsules (docs, pages, ready count, format), per-doc extraction coverage badges |
| Search | Full-text search across all documents with relevance ranking |
| Google Drive connector | OAuth-based Drive folder sync with auto-ingest and encrypted backup |
| Responsive design | Mobile-responsive across all views — chat, documents, dashboards |

### Deployment

| Capability | Detail |
|-----------|------------|
| Local deploy | `docker compose up` — single command, all services |
| Stack | postgres (pgvector) + redis + minio + backend (FastAPI) + worker (Arq) + frontend (Vite/React) |
| Resource requirements | 4 GB RAM minimum, 8 GB recommended; ~10 GB disk |
| Air-gapped capable | Hash embedding backend + local models — zero external calls |
| Configuration | Single `.env` file, 100+ knobs, sensible defaults for all |
| Platform | Linux, macOS (Docker); ARM64 and AMD64 |

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

## Quick start

### Prerequisites

- **Docker** + Docker Compose v2
- **4 GB RAM** minimum / **8 GB** recommended (multilingual embeddings use more memory)
- **~10 GB** free disk space (Docker images + database + file storage)
- **An LLM provider key** — you bring your own
- Python 3.11+
- Node.js 22+
- PostgreSQL with pgvector extension
- Redis

### Stack

- **Backend:** FastAPI + SQLAlchemy + Alembic + PostgreSQL (pgvector)
- **Worker:** Arq (Redis-backed async task queue)
- **Frontend:** Vite + React (SPA)
- **Storage:** MinIO (S3-compatible, local dev) / AWS S3

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

### Troubleshooting

| Symptom | Likely fix |
|---------|-----------|
| **Chat returns nothing** | Check your LLM provider key is set and funded. Try `curl` to the provider directly. |
| **Documents stuck "processing"** | Check worker logs: `docker compose -p docaiquest logs worker \| tail -30`. Usually a missing API key or rate limit. |
| **502 Bad Gateway** | Backend still booting — wait 10s and refresh. If it persists: `docker compose -p docaiquest logs backend \| tail -20`. |
| **Port 8085 already in use** | Change `FRONTEND_PORT` in `.env` to a different port. |
| **Out of disk space** | `docker builder prune -f --keep-storage 30GB && docker image prune -f` to reclaim build cache. |
| **Fresh start (wipe everything)** | `make down-clean && make up` — deletes all containers, volumes, and data. |


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

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

DocAIQuest is [MIT](LICENSE) licensed. See [NOTICE](NOTICE) for third-party attributions,
including model weights and bundled libraries.

---

Powered by DocAIQuest — [github.com/rbgoda/docaiquest](https://github.com/rbgoda/docaiquest)
