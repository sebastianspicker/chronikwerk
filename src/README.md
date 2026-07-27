# `src/`

This directory contains the service implementation.

High-level layout:

- `src/chronikwerk/runtime.py` – CLI/runtime entry point (loads config, configures logging, runs Uvicorn)
- `src/chronikwerk/asgi.py` – ASGI app module for `uvicorn chronikwerk.asgi:app`
- `src/chronikwerk/app/` – FastAPI app wiring, middleware, routes
  - `admin/` – feature-flagged HTML/API control plane, sessions, CSRF, and security headers
  - `routes/ingest.py` – `POST /ingest` webhook endpoint (`202` only after bounded admission; validation, authentication, or capacity failures return an error)
  - `routes/healthz.py` – `GET /healthz`
  - `routes/metrics.py` – `GET /metrics` (only mounted when enabled)
  - `middleware/` – request ID, HMAC verification, rate limit, body size limit
  - `jobs/process_ticket.py` – end-to-end ticket processing pipeline
  - `jobs/admission.py` – bounded process-local pending/running admission state
- `src/chronikwerk/adapters/` – external integrations and IO
  - `zammad/` – Zammad REST API client
  - `pdf/` – HTML rendering + PDF generation (WeasyPrint)
  - `signing/` – PAdES signing + RFC3161 TSA client (pyHanko)
  - `storage/` – path layout + atomic writes
  - `snapshot/` – snapshot builder
- `src/chronikwerk/domain/` – domain logic (path policy, audit sidecar schema, idempotency, state machine)
- `src/chronikwerk/config/` – settings, precedence, validation, and managed non-secret revisions
- `src/chronikwerk/i18n.py` – shared `de-DE` and `en-GB` catalogs
- `src/chronikwerk/templates/admin/` and `static/admin/` – server-rendered admin UI
  (CSS assembled from `frontend/admin/css/`, JS bundled from `frontend/admin.ts`)

Operator docs live in `docs/` (start with `docs/08-operations.md`).
