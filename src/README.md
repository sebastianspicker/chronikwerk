# `src/`

This directory contains the service implementation.

High-level layout:

- `src/zammad_pdf_archiver/runtime.py` – CLI/runtime entry point (loads config, configures logging, runs Uvicorn)
- `src/zammad_pdf_archiver/asgi.py` – ASGI app module for `uvicorn zammad_pdf_archiver.asgi:app`
- `src/zammad_pdf_archiver/app/` – FastAPI app wiring, middleware, routes
  - `admin/` – feature-flagged HTML/API control plane, sessions, CSRF, and security headers
  - `routes/ingest.py` – `POST /ingest` webhook endpoint (always returns 202; runs best-effort processing)
  - `routes/healthz.py` – `GET /healthz`
  - `routes/metrics.py` – `GET /metrics` (only mounted when enabled)
  - `middleware/` – request ID, HMAC verification, rate limit, body size limit
  - `jobs/process_ticket.py` – end-to-end ticket processing pipeline
  - `jobs/admission.py` – bounded process-local pending/running admission state
- `src/zammad_pdf_archiver/adapters/` – external integrations and IO
  - `zammad/` – Zammad REST API client
  - `pdf/` – HTML rendering + PDF generation (WeasyPrint)
  - `signing/` – PAdES signing + RFC3161 TSA client (pyHanko)
  - `storage/` – path layout + atomic writes
  - `snapshot/` – snapshot builder
- `src/zammad_pdf_archiver/domain/` – domain logic (path policy, audit sidecar schema, idempotency, state machine)
- `src/zammad_pdf_archiver/config/` – settings, precedence, validation, and managed non-secret revisions
- `src/zammad_pdf_archiver/i18n.py` – shared `de-DE` and `en-GB` catalogs
- `src/zammad_pdf_archiver/templates/admin/` and `static/admin/` – server-rendered admin UI

Operator docs live in `docs/` (start with `docs/08-operations.md`).
