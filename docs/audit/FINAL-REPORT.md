# Improvement Process Final Report

## Executive Summary

- **Date:** 2026-03-21
- **Total commits:** 41 (across 6 phases)
- **Total files changed:** 181
- **Total lines added:** ~13,278
- **Total lines removed:** ~3,181
- **Net lines:** +10,097
- **Test count:** before (273) → after (355) = **+82 tests**
- **Coverage:** before (79% line-only) → after (78% with branch coverage, threshold enforced at 76%)
- **Key achievements:** Tightened exception handling, extracted reusable patterns, hardened input validation and CI pipelines, added comprehensive tests, documented all endpoints and config fields, established enforced coverage thresholds with branch coverage enabled.

---

## Phase 1: Analysis & Inventory

- **18 issues identified** (1 HIGH, 6 MEDIUM, 7 LOW, 4 INFO)
- Key findings:
  - `/retry/{ticket_id}` lacks authentication (HIGH)
  - Test coverage at 79%, with critical gaps in `cli.py` (37%), `redis_queue.py` (53%), `healthz.py` (48%)
  - pyhanko pinned 6+ minor versions behind
  - 10 broad `except Exception` handlers across 7 files
  - Config reference mismatches and undocumented API endpoints
  - 1 mypy error, 1 ruff lint violation
- **Deliverables:** 5 sub-phase audit reports (static analysis baseline, coverage report, dependency audit, docs completeness, security review)

---

## Phase 2: Code Quality & Cleanup

- **Exception handling tightened:** 10 handlers across 5 files replaced with specific exception types (`ValueError`, `KeyError`, `FileNotFoundError`, `OSError`, etc.)
- **Type annotations added:** 3 files (`hmac_verify.py`, `server.py`, `settings.py`)
- **`async_retry` helper extracted:** Reusable decorator replacing inline retry/backoff logic in `process_ticket.py`
- **CLI error handling deduplicated:** Shared `cli_error_handler` decorator replacing repeated try/except/sys.exit patterns
- **Dead code removed:** `aclose_redis_stores` backwards-compatibility alias, E501 line-too-long fix in `render_pdf.py`
- **5 sub-phase commits**, 11 source files modified

---

## Phase 3: Testing & Coverage

- **82 new tests added** (273 → 355)
- **New test files:**
  - `test_async_retry.py` — 7 tests for retry/backoff decorator edge cases
  - `test_fs_storage.py` — 12 tests for filesystem storage adapter
  - `test_url_fetcher.py` — 9 tests for WeasyPrint URL fetcher adapter
  - `test_redis_pool.py` — 4 tests for Redis connection pool
  - `test_input_validation_hardening.py` — 9 tests for input validation (Phase 4)
- **Expanded test files:**
  - `test_cli.py` — 22 new tests (5 → 27 total)
  - `test_layout.py` — 8 new tests (9 → 17 total)
  - `test_config.py` — 5 new tests for Redis URL and rate-limit validation (Phase 4)
  - `test_redaction.py` — 2 new tests for credential scrubbing (Phase 4)
- **Coverage threshold:** 76% enforced in both `pyproject.toml` (`fail_under = 76`) and CI workflow (`--cov-fail-under=76`)
- **Branch coverage enabled:** `[tool.coverage.run] branch = true` — stricter metric than line-only

---

## Phase 4: Security & Hardening

- **Input validation:**
  - Positive `ticket_id` enforcement (`ge=1`) on all endpoints: `IngestPayload`, `/retry/{ticket_id}`, `/admin/api/retry/{ticket_id}`, `/admin/api/history`
  - Batch size limit: `MAX_BATCH_SIZE=100` on `/ingest/batch`
- **Config hardening:**
  - Redis URL scheme validation (`redis`/`rediss`/`unix` only)
  - Credential redaction for `redis_url` in settings dict, free-form text, and env-var output
  - Upper bounds on `rate_limit.rps` and `rate_limit.burst` (capped at 10,000)
- **CI hardening:**
  - `--no-cache-dir` on all pip install commands
  - GitHub Actions pinned to full SHA digests
  - `permissions: contents: read` (least privilege)
  - `persist-credentials: false` on all checkout steps
  - Job timeouts and concurrency groups with cancel-in-progress
  - `.dockerignore` added (prevents .git, .env, secrets in build context)
  - Docker Compose: `read_only`, `no-new-privileges`, `cap_drop` runtime hardening
  - `dependabot.yml` and `CODEOWNERS` added
- **Fixed pre-existing mypy error** in `tsa_rfc3161.py` (moved `auth` parameter to `AsyncClient` constructor)

---

## Phase 5: Documentation & Polish

- **34 docstrings added** across 5 core source modules (`process_ticket.py`, `redis_queue.py`, `fs_storage.py`, `build_snapshot.py`, `path_policy.py`)
- **Config reference:**
  - 3 missing fields added (`pdf.templates_root`, `signing.timestamp.rfc3161.user`, `signing.timestamp.rfc3161.password`)
  - 2 mislabeled entries fixed (`TSA_USER`/`TSA_PASS` moved from non-schema section to proper schema table)
  - `template_variant` comment corrected (`default|minimal` → `default|minimal|compact`)
- **API docs:**
  - 2 missing endpoints documented (`POST /admin/api/dlq/replay`, `GET /admin/api/config/check`)
  - 3 undocumented query parameters added (`dry_run` on `/ingest` and `/ingest/batch`, `deep` on `/healthz`)
  - Batch limit documented
- **CHANGELOG.md:** 21 entries under `[Unreleased]` across 5 categories (Added, Changed, Fixed, Security, Documentation)
- **Architecture doc:** `async_retry` module added to `docs/01-architecture.md`

---

## Phase 6: Integration & Verification

All 7 CI-equivalent steps passed without errors:

| Step | Check | Result |
|------|-------|--------|
| 1 | Ruff lint | PASS — 0 findings |
| 2 | Complexity budget (C901) | PASS — 0 violations |
| 3 | Mypy strict | PASS — 0 errors (157 source files) |
| 4 | Smoke test | PASS |
| 5 | Docs check | PASS |
| 6 | Pytest + coverage | PASS — 355 tests, 78% coverage (threshold: 76%) |
| 7 | Build (sdist + wheel) | PASS |

---

## Remaining Items

| Item | Severity | Notes |
|------|----------|-------|
| `/retry/{ticket_id}` lacks authentication | HIGH (F-01) | Intentionally deferred — requires design decision on whether to add HMAC or Bearer auth. The admin-scoped `/admin/api/retry/{ticket_id}` already has authentication. |
| pyhanko pinned 6+ minor versions behind | MEDIUM (F-08) | Current: 0.27.1, latest: 0.34.1. Upper bound `<0.28` blocks upgrade. Requires compatibility testing before bumping. |
| No lock file for reproducible installs | MEDIUM | Recommended: `uv lock` or `pip-compile` to pin transitive dependencies. |
| 3 modules at 0% coverage | LOW | Entry points: `__main__.py`, `asgi.py`, `runtime.py` — thin wrappers that delegate immediately. |

---

## Future Improvements

- **Add authentication to `/retry` endpoint** — evaluate HMAC signature verification vs. admin Bearer token approach
- **Bump pyhanko dependency** — test 0.34.x compatibility, update upper bound to `<0.35`
- **Add lock file for reproducible builds** — `uv lock` or `pip-compile` for deterministic CI and deployments
- **Increase coverage threshold** — raise from 76% as new tests are added (target: 85%)
- **Upper-bound major dependencies** — add `<1.0` caps to fastapi, pydantic, httpx, uvicorn
- **Move playwright to `[scripts]` group** — currently in `[dev]` but only used in `scripts/demo/`
