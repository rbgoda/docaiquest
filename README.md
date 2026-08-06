# DocAIQuest

**Open-source document intelligence engine.** A complete web app you self-host —
upload documents, chat with them, and extract structured data.
All through a browser. You bring your own LLM keys. Privacy-native by default.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

> **You need your own LLM provider key.** DocAIQuest OSS does not ship with
> managed LLM access. Set at least one of `DASHSCOPE_API_KEY` (recommended),
> `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `DEEPSEEK_API_KEY`,
> or `OPENROUTER_API_KEY` in your `.env` file before starting. Without a key,
> parsing and chunking work — but extraction and chat won't.

## What you get

DocAIQuest OSS is a **web console** you self-host — upload documents and chat
with them. Your own LLM keys, your own server, your data never leaves.

| Feature | Detail |
|---------|--------|
| **Upload documents** | Drag & drop PDF, DOCX, XLSX, CSV, PPTX, images, EML, HTML, TXT, and legacy Office formats |
| **View documents** | Rendered view with page navigation — see the original file as-is |
| **Chat with documents** | Ask questions about a document, get answers with source citations and page references |
| **Extract fields** | Pull out dates, amounts, parties, and line items from invoices, receipts, contracts, and more |
| **Search** | Full-text search across all your documents |
| **Multi-user** | Create accounts for your team — each user sees only their own documents |
| **API access** | One unified endpoint (`POST /api/v1`) + SDKs for Python and TypeScript |
| **AI assistant integration** | MCP server — connect ChatGPT, Claude, or Cursor directly to your documents |
| **Privacy built-in** | Sensitive info redacted before it reaches external AI providers |

### Supported file formats

| Format | Can upload? | Can chat with? | Can extract fields? |
|--------|:--:|:--:|:--:|
| PDF (text + scanned) | ✅ | ✅ | ✅ |
| DOCX (Word) | ✅ | ✅ | ✅ |
| XLSX / CSV / TSV | ✅ | ✅ | ✅ |
| PPTX (PowerPoint) | ✅ | ✅ | ✅ |
| Images (PNG, JPG, HEIC) | ✅ | ✅ | ✅ |
| EML (email) | ✅ | ✅ | ✅ |
| HTML | ✅ | ✅ | ✅ |
| TXT / Markdown | ✅ | ✅ | ✅ |
| Legacy Office (DOC, XLS, ODT, RTF) | ✅ | ✅ | ✅ |
| Audio / Video | ❌ | ❌ | ❌ |

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

Open **http://localhost:8085** in your browser. You should see the landing page.

### 3. Open the web app

Go to **http://localhost:8085** in your browser. You'll see the full DocAIQuest
interface — no terminal needed from here.

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
│  web :8085   │    │   API :8093  │    │  processing  │
└──────────────┘    └──────┬───────┘    └──────┬───────┘
                           │                   │
                    ┌──────┴───────┐    ┌──────┴───────┐
                    │  PostgreSQL  │    │  File storage │
                    │  (+ vectors) │    │  (S3 compat)  │
                    └──────────────┘    └──────────────┘
```

```
Upload → Parse → Chunk → Embed → Store
                                    ↓
Question → Search → Retrieve context → Generate answer
```

## Stack

Python · PostgreSQL · Redis · Docker Compose.

## License

DocAIQuest is [MIT](LICENSE) licensed. See [NOTICE](NOTICE) for third-party attributions,
including model weights and bundled libraries.

---

Powered by DocAIQuest — [github.com/rbgoda/docaiquest](https://github.com/rbgoda/docaiquest)
