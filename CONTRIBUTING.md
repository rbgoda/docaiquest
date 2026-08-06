# Contributing to DocAIQuest

Thanks for your interest in contributing! DocAIQuest is an open-source document
intelligence engine — ingest, parse, chunk, embed, retrieve, and extract from
documents, with AI-powered chat.

## Scope

This repository is the **engine** (`DOCAIQ_LICENSE_MODE=oss`): the complete
document → data pipeline. Users bring their own LLM API keys.

## Getting started

```bash
cp .env.example .env
# Set DOCAIQ_JWT_SECRET + at least one LLM provider key (DASHSCOPE_API_KEY, etc.)
make up          # Full stack at http://localhost:8085
make down        # Stop
```

See the [README](README.md) for detailed setup instructions.

## Stack

- **Backend:** FastAPI + SQLAlchemy + Alembic + PostgreSQL (pgvector)
- **Worker:** Arq (Redis-backed async task queue)
- **Frontend:** Vite + React (SPA)
- **Storage:** MinIO (S3-compatible, local dev) / AWS S3 (prod)

## Code conventions

- **Backend package** is named `app` (kept from the monorepo so imports never
  need rewriting).
- **Schema = Alembic** (`backend/migrations/`). Adding a table/column requires
  a new migration. Never use `create_all`.
- **Per-user isolation:** `current_owner_user_pk` ContextVar (TenantMiddleware)
  + repo-layer filtering. Never bypass it.
- **PII / privacy:** Redaction happens at the LLM boundary in
  `app/llm/gateway.py`. Product is privacy-first by default. If you change
  redaction, update `docs/PII_AND_PRIVACY.md`.
- Match surrounding code style: Python follows PEP 8, JSX uses the existing
  component patterns.

## Testing

Tests use a throwaway PostgreSQL + pgvector instance — never point at a live
database.

```bash
cd backend
# Create a throwaway DB, run tests, destroy
PGPASSWORD=postgres psql -h localhost -U postgres -c "CREATE DATABASE test_docaiq;"
pytest -q
PGPASSWORD=postgres psql -h localhost -U postgres -c "DROP DATABASE test_docaiq;"
```

There is no CI currently configured. Run tests locally before opening a PR.

## Pull request workflow

1. Branch from `main`
2. Make your change, write tests if applicable
3. Verify: `ruff check backend/app` (should exit 0)
4. Open a PR with a clear description of what changed and why

## Reporting issues

Use GitHub Issues. Include:
- Steps to reproduce
- Expected vs actual behavior
- Environment (OS, Python version, `pip freeze | head`)
- Relevant log output

## License

DocAIQuest is MIT licensed. By contributing, you agree that your contributions
will be licensed under the same MIT license. See [LICENSE](LICENSE) and
[NOTICE](NOTICE) for third-party attributions.
