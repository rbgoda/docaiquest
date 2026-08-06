# docaiquest — Python SDK

Official Python SDK for the [DocAIQuest](https://docaiq.jicama.tech) document-intelligence API.

## Install

```bash
pip install docaiquest
```

## Usage

```python
from docaiquest import Client

client = Client("dq_live_…")

# Grounded question-answering over your documents
print(client.ask("Which invoices are due this month?")["answer"])

# List your documents
for d in client.documents():
    print(d["name"])

# Extract structured fields from a file
print(client.extract("invoice.pdf")["fields"])
```

## Authentication

Every call is authenticated with an owner-scoped API key (looks like
`dq_live_…`) sent in the `X-API-Key` header. Pass it to the `Client`
constructor:

```python
client = Client("dq_live_…", base_url="https://docaiq.jicama.tech")
```

## Errors

Non-2xx responses raise `DocaiqError`, which carries `status_code` and the
API's `detail` message:

```python
from docaiquest import Client, DocaiqError

try:
    client.ask("…")
except DocaiqError as e:
    print(e.status_code, str(e))
```

## API reference

| Method | HTTP | Returns |
| --- | --- | --- |
| `ask(question, top_k=8)` | `POST /api/v1/ask` | `{answer, grounded, confidence, citations}` |
| `documents(limit=100)` | `GET /api/v1/documents?limit=` | list of `{id, name, type, createdAt}` |
| `extract(file_path)` | `POST /api/extraction/extract` | `{status, docType, fields, citations, confidence}` |
