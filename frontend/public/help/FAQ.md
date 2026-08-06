# DocAIQuest FAQ

This FAQ powers the in-app Help drawer (the **?** icon in the top bar) —
each `### Q:` heading becomes one collapsible entry.

If you add a question, follow the format: `### Q: <question text>`.

---

## Getting started

### Q: What is DocAIQuest?

An open-source document intelligence engine — upload documents, chat with them,
and extract structured data. Self-hosted, bring your own LLM keys, privacy-native.

### Q: What file formats are supported?

PDF, DOCX, XLSX, CSV/TSV, PPTX, images (PNG, JPG, HEIC), EML, HTML, TXT/Markdown,
and legacy Office formats (DOC, XLS, ODT, RTF) via LibreOffice conversion.
See the README for the full compatibility table.

### Q: How do I get an API key?

Sign in → user menu → **Settings → API keys** → **Create key**. The key is
owner-scoped (`dq_live_…`), shown once. Send it as `X-API-Key: dq_live_…`
or `Authorization: Bearer dq_live_…` on every request.

---

## Using the API

### Q: What can I call?

- `POST /api/v1/ask` — grounded Q&A over your documents, with citations and source spans.
- `GET /api/v1/documents` — list your documents.
- `POST /api/extraction/extract` — stateless file → structured fields (parsed and discarded).
- `POST /api/mcp` — MCP endpoint for AI assistants (Claude, ChatGPT, Cursor).

Interactive docs: **http://localhost:8085/api/docs**

### Q: Can I use it from ChatGPT or Claude?

Yes — MCP server at `http://localhost:8085/api/mcp`. Tools: `ask_documents`,
`list_documents`, `get_watchlist`. See README for the ChatGPT Custom GPT setup.

### Q: Are there SDKs?

Python (`pip install docaiquest`) and TypeScript (`@docaiquest/sdk`).
Source in `sdks/`.

### Q: Is my key scoped to only my data?

Yes. Owner-scoped keys can only read the creating user's documents.

---

## Documents & processing

### Q: What happens after I upload a document?

Backend pipeline runs in ~10-60 seconds:
1. Stored in blob storage (encrypted at rest).
2. Parsed — text extracted with OCR for scanned PDFs and images.
3. Chunked and embedded for retrieval.
4. Classified — AI guesses document type.
5. Fields extracted — typed data like dates, amounts, parties.

You'll see the status flip from `pending → processing → ready`.

### Q: Why did extraction produce no fields?

Two common causes:
1. **Type mismatch** — the classifier labeled the document as a type whose
   schema doesn't fit the content. Try checking the classified type.
2. **Low confidence** — the extractor wasn't sure. If you have a suitable
   schema, re-extract with that schema forced.

### Q: Can I edit the extracted markdown?

Yes — open a document, switch to the Markdown tab, toggle **Edit**, make
changes, then **Save & Reprocess** to re-chunk and re-embed from your edits.

---

## Chat

### Q: When I chat, what does the AI see?

The most relevant chunks from that document (hybrid BM25 + vector search,
re-ranked). For workspace chat, chunks from all your documents are searched.

### Q: Why did the chat give a wrong answer?

- The retrieved chunks might not contain the answer (retrieval gap).
- The LLM might have hallucinated despite grounding.
- Complex tables or multi-column layouts can confuse chunk boundaries.

Try rephrasing your question or asking for specific fields that are more
likely to be in the extracted data.

### Q: Why are some chats slow?

The first chat for a document loads the embedding model into memory (~10s).
Subsequent chats are faster. Enable `DOCAIQ_RERANKER_ENABLED=true` for
better quality at a small latency cost.

---

## Privacy

### Q: Is my data sent to the LLM provider?

Sensitive data is redacted BEFORE leaving your server — account numbers,
government IDs, emails, phone numbers, and street addresses are replaced
with placeholders like `[ACCOUNT_1]`. After the LLM responds, the original
values are restored. You see the real values; the provider never does.

Person names are NOT redacted by default (they're essential for search).
Set `DOCAIQ_PII_REDACT_PERSON_NAMES=true` to mask them too.

### Q: Where is my data stored?

Everything stays on your own server — PostgreSQL for metadata, MinIO (S3-compatible)
for files. If you configure Google Drive, originals are stored in YOUR Google Drive,
not ours.

---

## Troubleshooting

### Q: Chat returns nothing

Check your LLM provider key is set and funded. Verify with:
```bash
docker compose -p docaiquest logs worker | tail -30
```

### Q: Document stuck on "processing"

Worker might have crashed. Check:
```bash
docker compose -p docaiquest logs worker | tail -30
```
Usually a missing API key or rate limit. Restart:
```bash
docker compose -p docaiquest restart worker
```

### Q: I can't log in — keeps redirecting to login

Clear cookies for this domain. In local dev (HTTP), cookies should still work.
In production, make sure you're on HTTPS.

### Q: Out of disk space

```bash
docker builder prune -f --keep-storage 30GB
docker image prune -f
```

### Q: Fresh start (wipe everything)

```bash
make down-clean && make up
```
Deletes all containers, volumes, and data.

---

## Where to find more

- **README** — architecture, quickstart, development guide.
- **Swagger** — http://localhost:8085/api/docs — interactive API reference.
- **GitHub** — https://github.com/rbgoda/docaiquest
