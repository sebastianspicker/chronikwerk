# zammad-ticket-archiver

[![CI](https://github.com/sebastianspicker/zammad-ticket-archiver/actions/workflows/ci.yml/badge.svg)](https://github.com/sebastianspicker/zammad-ticket-archiver/actions/workflows/ci.yml)
[![Docker](https://github.com/sebastianspicker/zammad-ticket-archiver/actions/workflows/docker.yml/badge.svg)](https://github.com/sebastianspicker/zammad-ticket-archiver/actions/workflows/docker.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/downloads/)

FastAPI service for archiving Zammad tickets as PDFs on a filesystem target
(local disk, mounted SMB/CIFS share, or another mounted volume).

```text
webhook -> fetch ticket/articles/tags -> build snapshot -> render PDF
        -> optional PAdES signature/RFC3161 timestamp
        -> write PDF + audit sidecar -> update Zammad tags and note
```

## What It Does

- Receives Zammad webhooks at `POST /ingest` and `POST /ingest/batch`.
- Fetches ticket details, tags, and articles from the Zammad REST API.
- Renders PDFs with bundled Jinja2 templates and WeasyPrint.
- Optionally applies PAdES signatures and RFC3161 timestamps.
- Stores a PDF plus audit sidecar JSON next to it.
- Updates ticket tags and writes an internal processing note.

## Runtime Surface

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| `POST` | `/ingest` | HMAC | Accept one webhook payload. |
| `POST` | `/ingest/batch` | HMAC | Accept up to 100 webhook payloads. |
| `POST` | `/retry/{ticket_id}` | Bearer | Force one reprocessing attempt. |
| `GET` | `/jobs/history` | none | Read process-local processing history. |
| `GET` | `/healthz` | none | Health check; supports `?deep=true`. |
| `GET` | `/metrics` | optional Bearer | Prometheus metrics when enabled. |

See [docs/api.md](docs/api.md) for request and response details.

## Non-Goals

- Archive search or browsing UI.
- Built-in retention or WORM policy engine.
- Built-in encryption-at-rest management.
- Multi-tenant isolation beyond path policy and external filesystem ACLs.

## Quickstart

Requirements:

- Docker with Docker Compose v2
- A Zammad API token
- A storage path writable by the service

```bash
git clone https://github.com/sebastianspicker/zammad-ticket-archiver.git
cd zammad-ticket-archiver
cp .env.example .env
```

Edit `.env` with at least:

```bash
ZAMMAD__BASE_URL=https://your-zammad.example.com
ZAMMAD__API_TOKEN=your-api-token
ZAMMAD__WEBHOOK_HMAC_SECRET=your-webhook-secret
STORAGE__ROOT=/mnt/archive
```

Start and verify:

```bash
docker compose up -d --build
curl http://localhost:8080/healthz
```

The service is then ready for Zammad webhooks on `POST /ingest`.

## Zammad Workflow

Default trigger tag: `pdf:sign`.

Default required ticket fields:

- `archive_path`: target path segments under `storage.root`
- `archive_user_mode`: `owner`, `current_agent`, or `fixed`
- `archive_user`: required only when `archive_user_mode=fixed`

Tag transitions:

- Start: remove `pdf:error` and trigger tag, then add `pdf:processing`.
- Success: remove `pdf:processing`, `pdf:error`, and trigger tag, then add
  `pdf:signed`.
- Failure: remove `pdf:processing` and `pdf:signed`, then add `pdf:error`.
  Transient failures keep or re-add the trigger tag; permanent failures remove it.

## Development

```bash
python -m pip install -e ".[dev]"
make lint
make test-fast
make qa
make verify
```

Useful make targets:

| Target | Purpose |
| --- | --- |
| `make lint` | Ruff linting. |
| `make typecheck` | Mypy over `src` and `test`. |
| `make test-fast` | Static and unit tests. |
| `make test-all` | Static, unit, integration, and NFR tests. |
| `make docs-check` | Checks documented Markdown paths exist. |
| `make verify` | QA plus package build. |

## Documentation

- [Architecture](docs/01-architecture.md)
- [Zammad setup](docs/02-zammad-setup.md)
- [Data model](docs/03-data-model.md)
- [Path policy](docs/04-path-policy.md)
- [PDF rendering](docs/05-pdf-rendering.md)
- [Signing and timestamping](docs/06-signing-and-timestamp.md)
- [Storage](docs/07-storage.md)
- [Operations](docs/08-operations.md)
- [Security](docs/09-security.md)
- [API reference](docs/api.md)
- [Configuration reference](docs/config-reference.md)
- [Deployment](docs/deploy.md)
- [FAQ](docs/faq.md)
- [Release checklist](docs/release-checklist.md)

## Glossary

- **Audit sidecar**: JSON metadata written next to the PDF, including checksum
  and processing metadata.
- **Archive path**: ticket custom field used for path segments under
  `storage.root`.
- **Delivery ID**: `X-Zammad-Delivery` header used for best-effort in-memory
  deduplication.
- **PAdES**: PDF Advanced Electronic Signatures.
- **RFC3161**: Time-stamp protocol used by Time Stamping Authorities.
- **TSA**: Time Stamping Authority.
