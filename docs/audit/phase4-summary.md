# Phase 4 Summary: Security Hardening

**Date:** 2026-03-21
**Status:** Complete

## Overview

Phase 4 addressed security hardening across three sub-phases: CI/supply-chain
protections (4.1), input validation (4.2), and configuration validation with
secret redaction (4.3). A pre-existing mypy type error in `tsa_rfc3161.py` was
also resolved.

## Sub-Phase 4.1 — CI & Supply-Chain Hardening

**Commits:**
- `caaafa1` security: harden CI workflows and add supply-chain protections
- `9dc0c1e` security: harden CI workflows and container runtime
- `6fd6c3f` security: add --no-cache-dir to all CI pip install commands

**Changes:**
- Pin all GitHub Actions to full SHA digests (prevent tag-hijack attacks)
- Add top-level `permissions: contents: read` to ci.yml (least privilege)
- Set `persist-credentials: false` on all checkout steps
- Add `dependabot.yml` for automated actions and pip dependency updates
- Add `CODEOWNERS` for mandatory review on CI, Docker, and security paths
- Add job timeouts to all workflows (prevent CI abuse via hung jobs)
- Add concurrency groups with cancel-in-progress (prevent resource exhaustion)
- Scope CI push trigger to main branch only (reduce attack surface)
- Add `.dockerignore` (prevent .git, .env, secrets leaking into build context)
- Add `read_only`, `no-new-privileges`, `cap_drop` to docker-compose (runtime hardening)
- Add `--no-cache-dir` to all CI pip install commands (prevent cached package tampering)

**Files modified:**
- `.github/workflows/ci.yml`
- `.github/workflows/docker.yml`
- `.github/workflows/rc-release.yml`
- `.github/workflows/security.yml`
- `.github/CODEOWNERS`
- `.github/dependabot.yml` (new)
- `.dockerignore` (new)
- `docker-compose.yml`

## Sub-Phase 4.2 — Input Validation Hardening

**Commit:** `1ff6480` security: harden input validation across entry points

**Changes:**
- `IngestPayload.ticket_id`: add `Field(ge=1)` for schema-level rejection of non-positive IDs
- `batch_ingest`: add `MAX_BATCH_SIZE=100` limit (defense-in-depth against resource exhaustion)
- `retry_ticket` and `admin_retry_ticket`: add `Path(ge=1)` for positive integer enforcement
- `admin_history` ticket_id: add `Query(ge=1)` for positive integer enforcement
- Rate limit middleware: fall back to ASGI client address when header is absent/empty
  to prevent rate-limit bypass by omitting the spoofable header
- Body size limit middleware: document chunked transfer-encoding safety (ASGI decodes before app)

**Files modified:**
- `src/zammad_pdf_archiver/app/middleware/body_size_limit.py`
- `src/zammad_pdf_archiver/app/middleware/rate_limit.py`
- `src/zammad_pdf_archiver/app/routes/admin.py`
- `src/zammad_pdf_archiver/app/routes/ingest.py`

## Sub-Phase 4.3 — Configuration Validation & Secret Redaction

**Commit:** `2966f22` security: harden configuration validation and secret redaction

**Changes:**
- Add `redis_url` to sensitive key set in `redact_settings_dict`
- Redact Redis connection-string credentials (`redis://:pass@host`) in free-form text,
  JSON dumps, and env-var style output
- Validate Redis URL scheme (`redis`/`rediss`/`unix`) when Redis backends are configured,
  rejecting invalid schemes like `http://`
- Add upper bounds (`le=10_000`) to `rate_limit.rps` and `rate_limit.burst` to prevent
  misconfiguration

**Files modified:**
- `src/zammad_pdf_archiver/config/redact.py`
- `src/zammad_pdf_archiver/config/settings.py`
- `src/zammad_pdf_archiver/config/validate.py`

## Additional Fix — mypy Type Error

The pre-existing mypy `arg-type` error on `tsa_rfc3161.py:80` (reported in the Phase 3
summary) was resolved by moving the `auth` parameter from `client.post()` to the
`AsyncClient` constructor with an explicit type annotation that satisfies httpx's expected
signature.

**File modified:**
- `src/zammad_pdf_archiver/adapters/signing/tsa_rfc3161.py`

## New Tests Added

| File | New Tests | Purpose |
|------|-----------|---------|
| `test/unit/test_input_validation_hardening.py` (new) | 9 | Ticket ID validation (zero, negative, positive, nested), batch size limits, rate-limit header fallback |
| `test/unit/test_config.py` (expanded) | 5 | Redis URL scheme validation (invalid, valid, rediss), rate-limit rps/burst upper bounds |
| `test/unit/test_redaction.py` (expanded) | 2 | Redis URL key redaction, Redis credential scrubbing in free text |

**Total new tests from Phase 4:** 16

## Verification Results

| Check | Result |
|-------|--------|
| `ruff check .` | PASS (all checks passed) |
| `mypy . --config-file pyproject.toml` | PASS (0 errors, 157 files checked) |
| `make test-fast` | PASS (257 passed, 0 failed) |
| No secrets in recent diffs | PASS (scanned HEAD~5..HEAD, no hardcoded secrets found) |
| SHA-pinned GitHub Actions | PASS (all actions use full SHA digests) |
| `persist-credentials: false` on checkouts | PASS |
| `.dockerignore` present | PASS |
| `dependabot.yml` present | PASS |
| `CODEOWNERS` present | PASS |
| Input validation on all ticket_id params | PASS (`ge=1` on all entry points) |
| Batch size limit enforced | PASS (`MAX_BATCH_SIZE=100`) |
| Rate-limit header bypass prevented | PASS (falls back to ASGI client address) |
| Redis URL validation | PASS (scheme must be redis/rediss/unix) |
| Redis credentials redacted | PASS (in settings dict, free text, and env vars) |
| Rate-limit config upper bounds | PASS (rps and burst capped at 10,000) |
