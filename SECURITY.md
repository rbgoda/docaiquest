# Security Policy

## Supported versions

| Version | Supported |
|---------|-----------|
| latest `main` branch | ✅ |

We release security patches as commits to `main`. There are no backport
branches — update to the latest commit to receive fixes.

## Reporting a vulnerability

**Do not open a public issue.** Instead, email:

**security@docaiquest.dev**

You should receive an acknowledgment within 48 hours. We triage reports
within 5 business days and aim to publish fixes within 90 days.

### What to include

- Steps to reproduce
- Affected version (git commit hash)
- Any proof-of-concept code or screenshots
- Whether you believe the issue is already public

### Process

1. You report via email
2. We acknowledge within 48 hours
3. We investigate and confirm within 5 business days
4. We develop and test a fix
5. We release the fix and publish an advisory
6. We credit you in the advisory (unless you prefer anonymity)

## Security design

DocAIQuest is a **self-hosted** application. You control the deployment,
the network boundary, and the LLM provider keys. The following is your
responsibility as the operator:

- **TLS termination** — run behind a reverse proxy (nginx, Caddy) with
  HTTPS. DocAIQuest itself listens on plain HTTP.
- **LLM provider keys** — stored in your `.env` file. Never commit it.
- **Database credentials** — rotate defaults before exposing to a network.
- **JWT secret** — set `DOCAIQ_JWT_SECRET` to a long random string. The
  default is intentionally weak.
- **Network isolation** — the Compose file binds to `127.0.0.1` where
  possible. Don't expose internal services (postgres, redis, minio) to
  the public internet.

### Privacy by design

- **PII redaction** — sensitive identifiers and contact details are masked
  before they reach external LLM providers. See `.env.example` for the
  `DOCAIQ_PII_*` flags.
- **Your data, your disk** — documents, embeddings, and extracted fields
  stay in your own postgres and MinIO volumes.
- **No telemetry** — DocAIQuest OSS does not phone home.

### Supply chain

- Dependencies are pinned in `backend/pyproject.toml` and rebuilt via
  `docker compose build`.
- We do not ship pre-built binaries. You build from source.
