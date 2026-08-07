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

> **You need your own LLM provider key.** DocAIQuest OSS does not ship with
> managed LLM access.
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

| Format | Detail |
|--------|--------|
| PDF (text) | Layout preservation, table extraction |
| PDF (scanned/OCR) | External vision models |
| DOCX / PPTX | Native parsers with embedded-image OCR support |
| XLSX / CSV / TSV | Structured table extraction |
| Images (PNG, JPG, HEIC, AVIF) | Vision model OCR |
| EML (email) | Headers, body, and attachment extraction |
| TXT / Markdown | Encoding detection |

### Chunking & Embedding

| Capability | Detail |
|-----------|------------|
| Chunking | Block-aware + semantic chunking, configurable overlap windows |
| Embedding backends | Local (free, CPU), DashScope, OpenAI, Gemini — bring your own key |

### Retrieval

| Capability | Detail |
|-----------|------------|
| Hybrid retrieval | BM25 keyword + vector similarity, fused with Reciprocal Rank Fusion |
| Graph retrieval | Entity graph traversal across documents |
| Citations | Per-sentence source citations with in-page jump links |
| Abstention | Refuses to answer when evidence is insufficient |

### Extraction & Structured Data

| Capability | Detail |
|-----------|------------|
| Field extraction | Dates, amounts, parties, line items, IDs — from invoices, receipts, contracts, and more |
| Schema system | Built-in schemas for common document types (invoices, contracts, IDs) |
| Confidence scoring | Per-field confidence scores |

### Chat & Query

| Capability | Detail |
|-----------|------------|
| Single-document chat | RAG with citations — ask questions about one document |
| Cross-document chat | Ask across all your documents at once |
| Multi-turn conversations | Follow-up questions with full conversation history |

### Knowledge Graph

| Capability | Detail |
|-----------|------------|
| Entity extraction | Persons, orgs, dates, amounts, identifiers — extracted from every document |
| Cross-doc entity resolution | Same person or org recognized across documents |
| Entity profiles | Aggregated view of each entity across all documents |
| Graph retrieval | Combine vector search with entity graph traversal for richer answers |

### Multimodal & Vision

| Capability | Detail |
|-----------|------------|
| Image OCR | Vision model OCR for scanned documents and images |
| Table extraction | Tables rendered as HTML with in-page jump links |
| Figure extraction | Embedded images extracted from PDFs and Office docs |
| Office image OCR | OCR for images embedded in DOCX and PPTX files |

### Privacy & Security

| Capability | Detail |
|-----------|------------|
| Data residency | All data stays in your own database and file storage |
| No telemetry | Zero outbound calls beyond the LLM providers you configure |
| Per-user isolation | Each user sees only their own documents |

### API & SDK

| Capability | Detail |
|-----------|------------|
| REST API | Full OpenAPI (Swagger) at `/api/docs` — upload, extract, chat, search, list, export |
| Python SDK | `pip install docaiquest` — typed client for the extraction and chat API |
| TypeScript SDK | `npm install @docaiquest/sdk` — typed client for Node.js and browser |
| MCP server | Streamable HTTP JSON-RPC at `/api/mcp` — connect Claude, ChatGPT, or Cursor to your documents |

### Frontend & UX

| Capability | Detail |
|-----------|------------|
| Document viewer | Rendered view, raw Markdown with edit support, page navigation |
| Chat panel | Split-pane with source citations, inline charts |
| Search | Full-text search across all documents |
| Google Drive | OAuth-based folder sync and ingest |
| Responsive | Mobile-friendly across all views |

### Operations & Admin

| Capability | Detail |
|-----------|------------|
| Admin console | User management, reprocessing, analytics |
| Background jobs | Async ingestion, embedding, extraction, graph updates |
| LLM cost guard | Per-user hourly and daily caps |
| Feedback | Per-answer user feedback with screenshot capture |

### Deployment

| Capability | Detail |
|-----------|------------|
| Deploy | `docker compose up` — single command |
| Stack | PostgreSQL (pgvector) + Redis + MinIO + FastAPI + React |
| Requirements | 4 GB RAM minimum, 8 GB recommended; ~10 GB disk |
| Configuration | Single `.env` file |
| Platform | Linux, macOS (Docker); ARM64 and AMD64 |

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

Starts 6 services. On first boot, database tables are created automatically.
Wait ~30 seconds for everything to settle, then open **http://localhost:8085**.

### 2. Verify

```bash
curl http://localhost:8085/api/health
# → {"status":"ok","tenant":"default","environment":"local","license_mode":"oss"}
```

### 3. Usage

1. **Sign up** — create an account (auto-verified in local mode).
2. **Upload** — drag a file onto the upload area. Processing begins automatically (10–60s).
3. **Chat** — click the document and ask questions. "Summarize this document."
4. **Explore** — browse extracted fields, entities, and the knowledge graph.

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


### Conventions

- **Schema = Alembic** — adding a table or column requires a new migration. Never use `create_all()`.
- **Per-user isolation** — every query is filtered by owner. Never bypass it.

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
