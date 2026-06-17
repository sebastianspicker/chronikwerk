# zammad-ticket-archiver

[![CI](https://github.com/sebastianspicker/zammad-ticket-archiver/actions/workflows/ci.yml/badge.svg)](https://github.com/sebastianspicker/zammad-ticket-archiver/actions/workflows/ci.yml)
[![Docker](https://github.com/sebastianspicker/zammad-ticket-archiver/actions/workflows/docker.yml/badge.svg)](https://github.com/sebastianspicker/zammad-ticket-archiver/actions/workflows/docker.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/downloads/)

`zammad-ticket-archiver` is a FastAPI webhook service that archives Zammad tickets as PDF files on a filesystem target (local path or mounted CIFS/SMB).

Processing pipeline:

```
webhook -> fetch ticket data -> build snapshot -> render PDF
        -> optional sign -> optional timestamp
        -> store PDF + audit sidecar -> update ticket tags + note
```

## What It Does

- Exposes `POST /ingest` to receive Zammad webhooks.
- Fetches ticket, tags, and articles from Zammad REST API.
- Renders a PDF with Jinja2 templates + WeasyPrint.
- Optionally applies:
  - PAdES signature (PKCS#12/PFX)
  - RFC3161 timestamp token (TSA)
- Writes two files:
  - PDF
  - audit sidecar JSON (`<filename>.json`, for PDFs usually `...pdf.json`)
- Writes an internal note to the ticket and transitions archive tags.

## Scope

This repository provides:

- FastAPI service endpoints:
  - `POST /ingest`
  - `POST /ingest/batch`
  - `POST /retry/{ticket_id}`
  - `GET /jobs/history`
  - `GET /healthz`
  - `GET /metrics` (when enabled)
- End-to-end ticket processing:
  1. receive webhook
  2. fetch ticket + tags + articles from Zammad
  3. build normalized snapshot model
  4. render PDF
  5. optionally sign and timestamp
  6. write PDF + sidecar JSON to storage
  7. update ticket note + tags
- Runtime hardening controls:
  - webhook HMAC verification
  - optional delivery ID requirement
  - request size limits
  - rate limiting
  - transport safety checks for upstream URLs

## Non-goals

Out of scope by design:

- archive browsing/search UI
- built-in retention/WORM policy engine
- built-in encryption-at-rest management
- multi-tenant isolation beyond path policy and external ACLs

## How Archiving Works

### Trigger Tag

Default trigger tag is `pdf:sign` (`workflow.trigger_tag`).

Processing behavior:
- `workflow.require_tag=true` (default): ticket is processed only when trigger tag is present.
- If ticket already has `pdf:signed`, processing is skipped.
- `POST /retry/{ticket_id}` forces one reprocessing run even when the trigger tag is absent or `pdf:signed` is already present.

### Required Ticket Fields

Defaults are configurable for the first two fields:
- `archive_path` (`fields.archive_path`, required)
- `archive_user_mode` (`fields.archive_user_mode`, optional, default `owner`)

`archive_user_mode` values:
- `owner`: use `ticket.owner.login`
- `current_agent`: use webhook `payload.user.login`, fallback `ticket.updated_by.login`
- `fixed`: use the custom field configured as `fields.archive_user` (default `archive_user`; required in this mode)

The field names for `archive_path`, `archive_user_mode`, and `archive_user` are configurable via `fields.*` in config or `FIELDS_ARCHIVE_PATH`, `FIELDS_ARCHIVE_USER_MODE`, `FIELDS_ARCHIVE_USER` in the environment.

### Tag State Transitions

- Start: `apply_processing()`
  - normal ingest: remove `pdf:error`, trigger tag
  - explicit retry/reprocess: also remove `pdf:signed`
  - add `pdf:processing`
- Success: `apply_done()`
  - remove `pdf:processing`, `pdf:error`, trigger tag
  - add `pdf:signed`
- Failure: `apply_error()`
  - remove `pdf:processing`, `pdf:signed`
  - add `pdf:error`
  - transient failures keep/re-add trigger tag
  - permanent failures remove trigger tag

## Architecture Overview

```mermaid
flowchart LR
  Z["Zammad"] -->|"Webhook: POST /ingest"| I["FastAPI ingress"]
  D -->|"inprocess"| J["process_ticket worker"]
  Q --> W["Queue worker"]
  W --> J
  J --> ZA["Zammad API adapter"]
  ZA --> SN["Snapshot builder"]
  SN --> PDF["Jinja2 + WeasyPrint"]
  PDF --> SG["pyHanko signer (optional)"]
  SG --> TSA["RFC3161 TSA (optional)"]
  PDF --> ST["Storage adapter"]
  SG --> ST
  J --> ZA
```

Detailed architecture and state diagrams:
- [`docs/01-architecture.md`](docs/01-architecture.md)

## High-Level Behavior

```mermaid
flowchart TD
  A["Agent macro adds trigger tag (pdf:sign)"] --> B["Zammad trigger sends webhook"]
  B --> C["POST /ingest returns 202"]
  C --> D{"Execution backend"}
  D --> E["In-process worker"]
  E --> G["Ticket processing pipeline"]
  F --> G
  G --> H["PDF + sidecar written"]
  G --> I["History event recorded (optional)"]
  G --> J["Ticket note + final tags"]
```

## Quickstart (Docker Compose)

Prerequisites:
- Docker with `docker compose`

**1. Clone and configure:**

```bash
git clone https://github.com/sebastianspicker/zammad-ticket-archiver.git
cd zammad-ticket-archiver
cp .env.example .env
```

**2. Edit `.env` with your values** (minimum required):

```bash
ZAMMAD__BASE_URL=https://your-zammad.example.com
ZAMMAD__API_TOKEN=your-api-token
STORAGE__ROOT=/mnt/archive
ZAMMAD__WEBHOOK_HMAC_SECRET=your-webhook-secret
```


**3. Start the service:**

```bash
docker compose up -d --build
```

**4. Verify the service is running:**

```bash
curl http://localhost:8080/healthz
# {"status":"ok","service":"zammad-pdf-archiver","version":"...","time":"..."}
```

The service is now ready to receive webhooks from Zammad on `POST /ingest`.

### Development Setup

For local development (lint, test, type-check):

```bash
# Install with dev dependencies
pip install -e ".[dev]"

# Start dev stack (hot-reload)
make dev

# Run validation
make lint          # ruff
make test          # pytest
make qa            # full QA (lint + mypy + all tests)
make verify        # QA + build
```

### Local Demo (Self-Contained)

Run a fully self-contained demo with a mock Zammad API:

```bash
make demo-seed     # seed demo ticket data
make demo-down     # tear down
```

### Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/ingest` | HMAC | Webhook ingestion |
| `POST` | `/ingest/batch` | HMAC | Batch webhook ingestion (max 100) |
| `POST` | `/retry/{ticket_id}` | Bearer | Re-process a ticket |
| `GET` | `/jobs/history` | -- | Process-local processing history |
| `GET` | `/healthz` | -- | Health check (supports `?deep=true`) |
| `GET` | `/metrics` | Optional Bearer | Prometheus metrics (optional) |

See [`docs/api.md`](docs/api.md) for full request/response details.

## Configuration

Precedence (highest first):
1. Environment variables (including values loaded from `.env`)
2. YAML config (`CONFIG_PATH`, or `config/config.yaml` when present)
3. Defaults in settings model

### Configuration Reference

| Resource | Description |
|----------|-------------|
| [`.env.example`](.env.example) | Annotated environment variable template |
| [`config/config.example.yaml`](config/config.example.yaml) | Full YAML configuration example |
| [`docs/config-reference.md`](docs/config-reference.md) | Complete key reference with defaults and env keys |

## Operational Notes

- All output paths are validated and confined under `storage.root`.
- Default storage writes are atomic and fsynced (`storage.fsync=true`).
- Signing requires `signing.enabled=true` and `signing.pfx_path`.
- Timestamping requires signing plus:
  - `signing.timestamp.enabled=true`
  - `signing.timestamp.rfc3161.tsa_url`
- TSA basic auth (if needed) uses env-only keys:
  - `TSA_USER`
  - `TSA_PASS`
- For `POST /ingest/batch`, a batch-level `X-Zammad-Delivery` header is expanded to per-item IDs of the form `<delivery-id>:<index>` before dedupe is applied.
- If a ticket is stuck in `pdf:processing` after a crash, see [Stuck in pdf:processing](docs/faq.md#why-is-a-ticket-stuck-with-pdfprocessing) in the FAQ.

Operational docs:
- [`docs/04-path-policy.md`](docs/04-path-policy.md)
- [`docs/06-signing-and-timestamp.md`](docs/06-signing-and-timestamp.md)
- [`docs/07-storage.md`](docs/07-storage.md)
- [`docs/08-operations.md`](docs/08-operations.md)
- [`docs/09-security.md`](docs/09-security.md)

## Validation Commands

```bash
make lint          # ruff linting
make test          # pytest (all tests)
make test-fast     # static + unit tests only
make qa            # full QA: lint + mypy + all test suites
make verify        # QA + sdist/wheel build
make smoke         # smoke test (requires running service)
make dev           # Docker Compose dev stack (hot-reload)
```

## Documentation (index)

- [`docs/01-architecture.md`](docs/01-architecture.md)
- [`docs/02-zammad-setup.md`](docs/02-zammad-setup.md)
- [`docs/03-data-model.md`](docs/03-data-model.md)
- [`docs/04-path-policy.md`](docs/04-path-policy.md)
- [`docs/05-pdf-rendering.md`](docs/05-pdf-rendering.md)
- [`docs/06-signing-and-timestamp.md`](docs/06-signing-and-timestamp.md)
- [`docs/07-storage.md`](docs/07-storage.md)
- [`docs/08-operations.md`](docs/08-operations.md)
- [`docs/09-security.md`](docs/09-security.md)
- [`docs/api.md`](docs/api.md)
- [`docs/config-reference.md`](docs/config-reference.md)
- [`docs/faq.md`](docs/faq.md)
- [`docs/release-checklist.md`](docs/release-checklist.md) – Release and deployment checklist
- [`docs/deploy.md`](docs/deploy.md) – Production deployment

## Glossary

- **Audit sidecar**: JSON file written next to each PDF containing checksum and processing metadata.
- **Archive path**: ticket custom field defining path segments under storage root.
- **Archive user mode**: strategy that selects the first directory component (`owner`, `current_agent`, `fixed`).
- **Delivery ID**: `X-Zammad-Delivery` header used for best-effort in-memory deduplication.
- **PAdES**: PDF Advanced Electronic Signatures profile.
- **RFC3161**: timestamp protocol used by Time Stamping Authorities.
- **TSA**: Time Stamping Authority endpoint used for timestamp tokens.
