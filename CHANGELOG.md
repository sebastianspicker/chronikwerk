# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0rc2] - 2026-04-09

### 0.2.0rc2 Added

- Coverage threshold enforcement in CI (76% minimum with branch coverage)
- Batch size limit (100 items) on POST /ingest/batch endpoint
- Redis URL scheme validation (redis://, rediss://, unix://)
- Redis URL credential redaction in logs and config dumps
- Async retry helper utility for exponential backoff
- 82+ new tests across CLI, adapters, input validation, and async retry

### 0.2.0rc2 Changed

- Tightened exception handling in CLI, sanitizer, and adapter modules
- Deduplicated CLI error handling with shared decorator
- Extracted async_retry helper from process_ticket
- Improved type annotations across server, settings, and middleware
- Rate limit header fallback prevents bypass when header is missing

### 0.2.0rc2 Fixed

- Pre-existing mypy arg-type error in tsa_rfc3161.py (auth parameter placement)
- Line-too-long lint violation in render_pdf.py

### 0.2.0rc2 Security

- Positive integer validation on all ticket_id parameters
- Rate limit `rps` and `burst` upper bounds (le=10000)
- `--no-cache-dir` on all CI pip install commands
- Input validation hardening across webhook and admin endpoints

### 0.2.0rc2 Documentation

- Added 34 missing docstrings across core source modules
- Updated config-reference.md with missing fields (pdf.templates_root, TSA user/password)
- Updated api.md with 2 missing admin endpoints and 3 undocumented query parameters
- Fixed template_variant comment to include all variants

## [0.2.0-rc.1] - 2026-02-26

### 0.2.0-rc.1 Added

- Redis-backed job history stream with API and CLI access (`/jobs/history`, `queue-history`).
- Dead-letter queue drain operations for jobs and admin APIs.
- Optional admin dashboard and admin API surface (`/admin`, `/admin/api/*`) protected by bearer token.
- Additive configuration keys for admin and workflow history (`admin.*`, `workflow.history_*`).
- Additional regression tests for cancellation flow, template-root rendering, and history redaction.

### 0.2.0-rc.1 Changed

- Refactored ticket processing and queue modules to reduce complexity and improve failure isolation.
- Hardened job/admin routes with clearer `401`/`503` behavior on auth/backend failures.
- Improved PDF template styling consistency across default, compact, and minimal variants.
- Updated CI/QA gates with docs check, complexity check (`C901`), and Dockerfile.dev smoke validation.

## [0.1.0] - 2026-02-07

### 0.1.0 Added

- FastAPI ingress endpoint (`POST /ingest`) with optional HMAC verification.
- Zammad API client integration for reading tickets and writing internal notes/tags.
- Snapshot model + template-based HTML rendering + PDF generation (WeasyPrint).
- Optional PAdES signing (pyHanko) and RFC3161 timestamping (TSA).
- Atomic storage writes for PDFs and audit sidecar JSON.
- Ops scripts for signature verification and CIFS mount helpers.
- Unit and integration test suite.
- Complete English documentation in `docs/`.
