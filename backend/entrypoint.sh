#!/bin/sh
# Container entrypoint: apply migrations, then start uvicorn.
# Migrations are idempotent — `upgrade head` is a no-op on an already-current DB.
set -e

echo "[entrypoint] Applying migrations…"
alembic upgrade head

echo "[entrypoint] Starting uvicorn…"
exec uvicorn app.main:app --host 0.0.0.0 --port 8001
