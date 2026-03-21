# Phase 5 Summary: Documentation Improvements

**Date:** 2026-03-21
**Status:** Complete

## Overview

Phase 5 addressed documentation gaps identified in the `docs/audit/docs-completeness.md`
audit report. Work was split across four sub-phases: config-reference fixes (5.1), API
documentation fixes (5.2), source-code docstrings (5.3), and changelog/architecture
updates (5.4).

## Sub-Phase 5.1 — Config Reference Fixes

**Commit:** `1a5cf07` docs: update config-reference to match all settings fields

**Changes:**
- Added `pdf.templates_root` row to the `pdf` settings table in `config-reference.md`
  (default `null`, env alias `TEMPLATES_ROOT`, overrides built-in template directory)
- Moved `TSA_USER` and `TSA_PASS` out of the "Non-schema Runtime Environment Keys"
  section and into the `signing.timestamp.rfc3161` table as proper schema fields:
  - `signing.timestamp.rfc3161.user` (default `null`, env alias `TSA_USER`)
  - `signing.timestamp.rfc3161.password` (default `null`, env alias `TSA_PASS`, SecretStr)
- Fixed `PdfSettings.template_variant` inline comment in `settings.py` from
  `# default|minimal` to `# default|minimal|compact`

**Files modified:**
- `docs/config-reference.md`
- `src/zammad_pdf_archiver/config/settings.py`

## Sub-Phase 5.2 — API Documentation Fixes

**Commit:** `cc0b639` docs: update API documentation to match all endpoints

**Changes:**
- Documented `POST /admin/api/dlq/replay` endpoint (replays DLQ entries, accepts
  `limit` query param, requires admin auth)
- Documented `GET /admin/api/config/check` endpoint (returns config validation
  status with `valid`, `issues`, and `checks` fields, requires admin auth)
- Documented `dry_run` query parameter on `POST /ingest` and `POST /ingest/batch`
  (returns `202` with `dry_run_accepted` status without dispatching work)
- Documented `deep` query parameter on `GET /healthz` (performs Redis ping and
  storage write test, may return `degraded` status)
- Added 119 lines of new API documentation

**Files modified:**
- `docs/api.md`

## Sub-Phase 5.3 — Source Code Docstrings

**Commit:** `cacfc92` docs: add missing docstrings across source modules

**Changes:**
- Added 34 missing docstrings across 5 core source modules
- Covered domain-layer utilities, adapter internals, and job-processing functions

**Docstrings added by module:**

| File | Functions/Classes | Count |
|------|-------------------|------:|
| `app/jobs/process_ticket.py` | `_bound_context`, `_submit_job`, `_process_one`, `_persist_outputs`, `_write_note` and others | 13 |
| `app/jobs/redis_queue.py` | `_worker_loop`, `_claim_stale`, `_process_pending`, `_poll_new`, `_ack_message` and others | 10 |
| `adapters/storage/fs_storage.py` | `_write_tmp_file`, `ensure_dir`, `write_bytes`, `write_atomic_bytes` | 4 |
| `adapters/snapshot/build_snapshot.py` | `_article_to_snapshot`, `ZammadSnapshotClient`, `build_snapshot`, `enrich_attachment_content` and others | 5 |
| `domain/path_policy.py` | `ensure_within_root`, `sanitize_component` | 2 |
| **Total** | | **34** |

**Files modified:**
- `src/zammad_pdf_archiver/app/jobs/process_ticket.py`
- `src/zammad_pdf_archiver/app/jobs/redis_queue.py`
- `src/zammad_pdf_archiver/adapters/storage/fs_storage.py`
- `src/zammad_pdf_archiver/adapters/snapshot/build_snapshot.py`
- `src/zammad_pdf_archiver/domain/path_policy.py`

## Sub-Phase 5.4 — Changelog & Architecture Updates

**Commit:** `2d1e705` docs: update changelog and architecture docs for improvement cycle

**Changes:**
- Added comprehensive `[Unreleased]` section to `CHANGELOG.md` covering all
  improvements from Phases 1-5:
  - **Added:** 6 entries (coverage enforcement, batch limits, Redis validation,
    credential redaction, async retry helper, 82+ new tests, audit reports)
  - **Changed:** 5 entries (exception handling, CLI deduplication, async_retry
    extraction, type annotations, rate-limit hardening)
  - **Fixed:** 2 entries (mypy arg-type error, lint violation)
  - **Security:** 4 entries (positive integer validation, rate-limit bounds,
    no-cache-dir, input validation hardening)
  - **Documentation:** 4 entries (docstrings, config-reference, api.md, comment fix)
- Updated `docs/01-architecture.md` with notes on the improvement cycle

**Files modified:**
- `CHANGELOG.md`
- `docs/01-architecture.md`

## Verification Results

| Check | Result |
|-------|--------|
| `make docs-check` | PASS |
| `CHANGELOG.md` has `[Unreleased]` entries | PASS (21 entries across 5 categories) |
| `config-reference.md` contains `pdf.templates_root` | PASS (line 101) |
| `config-reference.md` contains `signing.timestamp.rfc3161.user` | PASS (line 136) |
| `config-reference.md` contains `signing.timestamp.rfc3161.password` | PASS (line 137) |
| `api.md` contains `POST /admin/api/dlq/replay` | PASS (lines 275, 278) |
| `api.md` contains `GET /admin/api/config/check` | PASS (lines 276, 304) |
| `_bound_context` has docstring | PASS ("Build structlog context vars for the current ticket job.") |
| `_worker_loop` has docstring | PASS ("Main consumer loop: claim stale messages, process pending, then poll for new ones.") |
| `_write_tmp_file` has docstring | PASS ("Write data to the temp fd with correct permissions, optionally fsyncing.") |
| `_article_to_snapshot` has docstring | PASS ("Convert a Zammad API article into a domain-layer Article with sanitized HTML/text.") |
| `ensure_within_root` has docstring | PASS ("Raise ValueError if target resolves outside of root (path-traversal guard).") |

## Summary Metrics

| Metric | Value |
|--------|------:|
| Docstrings added | 34 |
| Config-reference rows added/fixed | 3 |
| API endpoints documented | 2 |
| API query parameters documented | 3 |
| Changelog entries added | 21 |
| Source files modified (docstrings) | 5 |
| Documentation files modified | 4 |
