# @docaiq/sdk — TypeScript / JavaScript SDK

Official SDK for the [DocAIQ](https://docaiq.jicama.tech) document-intelligence API.
Zero runtime dependencies — uses the global `fetch` and `FormData` (Node 18+ and
modern browsers).

## Install

```bash
npm i @docaiq/sdk
```

## Usage

```js
import { DocaiqClient } from "@docaiq/sdk";

const client = new DocaiqClient({ apiKey: "dq_live_…" });

// Grounded question-answering over your documents
const res = await client.ask("Which invoices are due this month?");
console.log(res.answer);

// List your documents
for (const d of await client.documents()) {
  console.log(d.name);
}

// Extract structured fields from a file (Blob or File)
const result = await client.extract(fileBlob, "invoice.pdf");
console.log(result.fields);
```

## Authentication

Every call is authenticated with an owner-scoped API key (looks like `dq_live_…`)
sent in the `X-API-Key` header. Pass it to the constructor:

```js
const client = new DocaiqClient({
  apiKey: "dq_live_…",
  baseUrl: "https://docaiq.jicama.tech",
});
```

## Errors

Non-2xx responses throw an `Error` whose message is the API's `detail` field.

```js
try {
  await client.ask("…");
} catch (err) {
  console.error(err.message);
}
```

## API reference

| Method | HTTP | Returns |
| --- | --- | --- |
| `ask(question, { topK })` | `POST /api/v1/ask` | `{ answer, grounded, confidence, citations }` |
| `documents({ limit })` | `GET /api/v1/documents?limit=` | array of `{ id, name, type, createdAt }` |
| `extract(file, filename)` | `POST /api/extraction/extract` | `{ status, docType, fields, citations, confidence }` |
