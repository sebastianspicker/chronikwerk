# Phase 1 Audit Summary

**Date:** 2026-03-21
**Repository:** zammad-ticket-archiver
**Reviewed commit:** a3fc68e (main)

---

## Sub-Phase Status

| # | Sub-Phase | File | Status |
|---|-----------|------|--------|
| 1 | Static Analysis Baseline | `docs/audit/static-analysis-baseline.md` | COMPLETE |
| 2 | Test Coverage Report | `docs/audit/coverage-report.md` | COMPLETE |
| 3 | Dependency Audit | `docs/audit/dependency-audit.md` | COMPLETE |
| 4 | Documentation Completeness | `docs/audit/docs-completeness.md` | COMPLETE |
| 5 | Security Review | `docs/audit/security-review.md` | COMPLETE |

---

## Key Findings (Priority-Ranked)

### HIGH

| ID | Source | Finding |
|----|--------|---------|
| F-01 | Security | **`/retry/{ticket_id}` lacks authentication.** Not in `INGEST_PROTECTED_PATHS`; any network-reachable client can trigger ticket reprocessing without HMAC or bearer token verification. |

### MEDIUM

| ID | Source | Finding |
|----|--------|---------|
| F-07 | Coverage | **Overall test coverage at 79%**, 6 points below the recommended 85% threshold. Four critical modules are well under target: `cli.py` (37%), `redis_queue.py` (53%), `healthz.py` (48%), `admin.py` (68%). |
| F-08 | Dependencies | **pyhanko pinned 6 minor versions behind** (0.27.1 installed, 0.34.1 available). Upper bound `<0.28` blocks upgrade; same for pyhanko-certvalidator (0.26.8 vs 0.30.1). |
| F-09 | Dependencies | **Loose lower bounds** on fastapi, pydantic, pydantic-settings, uvicorn, httpx lack upper-bound caps, risking breakage on future major releases. |
| F-10 | Docs | **Config reference mismatches:** `pdf.templates_root` missing from config-reference.md; `TSA_USER`/`TSA_PASS` mislabeled as non-schema keys instead of documented in the `signing.timestamp.rfc3161` table. |
| F-11 | Docs | **Two admin API endpoints undocumented** in `api.md`: `POST /admin/api/dlq/replay` and `GET /admin/api/config/check`. |
| F-12 | Static | **10 tightenable `except Exception` handlers** across 7 files that could use more specific exception types. |

### LOW

| ID | Source | Finding |
|----|--------|---------|
| F-02 | Security | **Deep health check leaks filesystem path** (`?deep=true` exposes storage root and truncated exception messages). |
| F-03 | Security | **Metrics endpoint unprotected by default** when `metrics_bearer_token` is not configured. |
| F-13 | Docs | **Undocumented query parameters**: `dry_run` on `/ingest` and `/ingest/batch`; `deep` on `/healthz`. |
| F-14 | Docs | **`src/README.md` stale**: missing `routes/jobs.py` and `routes/admin.py` added in 0.2.0-rc.1. |
| F-15 | Dependencies | **pip-audit `|| true`** suppresses tool crashes silently; should distinguish exit code 1 (vulns found) from other codes (tool failure). |
| F-16 | Dependencies | **playwright in `[dev]`** but only used in `scripts/demo/`, not tests. Should move to a `[scripts]` optional group. |
| F-17 | Static | **1 mypy error**: `arg-type` in `tsa_rfc3161.py:80` (auth tuple type mismatch). |
| F-18 | Static | **1 ruff issue**: E501 line-too-long in `render_pdf.py:19`. |

### INFORMATIONAL

| ID | Source | Finding |
|----|--------|---------|
| F-04 | Security | SHA-1 HMAC still accepted (not a practical risk for HMAC constructions). |
| F-05 | Security | TOCTOU race in symlink rejection is mitigated by `O_NOFOLLOW` (defense-in-depth). |
| F-06 | Security | Rate limit bypass via spoofed `X-Forwarded-For` header in proxy-less deployments (documented trade-off). |
| F-19 | Docs | `settings.py` inline comment says `default|minimal` but `compact` variant also exists. |

---

## Issue Count by Severity

| Severity | Count |
|----------|------:|
| HIGH | 1 |
| MEDIUM | 6 |
| LOW | 7 |
| INFORMATIONAL | 4 |
| **Total** | **18** |

---

## Phase 2: Code Quality

Items to address in the code quality improvement phase.

### Exception Handling (10 items)

Tighten 10 `except Exception` handlers to use specific exception types:

| File | Line | Suggested Replacement |
|------|------|-----------------------|
| `domain/audit.py` | 23 | `PackageNotFoundError` |
| `cli.py` | 40 | `ValueError, pydantic.ValidationError` |
| `cli.py` | 54 | `ValueError, pydantic.ValidationError, FileNotFoundError` |
| `adapters/storage/fs_storage.py` | 75 | `ValueError` |
| `adapters/storage/layout.py` | 112 | `KeyError, IndexError, TypeError` |
| `app/jobs/history.py` | 79 | `ValueError, TypeError` |
| `app/jobs/history.py` | 88 | `ValueError, TypeError` |
| `app/jobs/redis_queue.py` | 131 | `ValueError, TypeError` |
| `app/jobs/redis_queue.py` | 138 | `ValueError, TypeError` |
| `adapters/pdf/template_engine.py` | 89 | `KeyError, ValueError, OverflowError` |

### Type Safety (1 item)

- Fix mypy `arg-type` error in `tsa_rfc3161.py:80`: auth tuple type needs explicit cast or narrower type annotation.

### Lint Cleanup (1 item)

- Fix E501 line-too-long in `render_pdf.py:19` (113 > 100 chars).

### Complexity

- No C901 violations. No flake8-bugbear violations. Codebase complexity is within acceptable limits.

---

## Phase 3: Testing

Items to address in the testing improvement phase. Current overall coverage: **79%** (target: 85%).

### Critical Coverage Gaps

| File | Current | Target | Missed Stmts | Estimated Overall Gain |
|------|--------:|-------:|--------------:|-----------------------:|
| `app/jobs/redis_queue.py` | 53% | 85% | 175 | +5.0% |
| `cli.py` | 37% | 60% | 74 | +2.0% |
| `app/routes/healthz.py` | 48% | 85% | 25 | +0.7% |
| `app/routes/admin.py` | 68% | 85% | 32 | +0.9% |
| `app/jobs/ticket_path.py` | 61% | 85% | 17 | +0.5% |

### Moderate Gaps

| File | Current | Notes |
|------|--------:|-------|
| `app/jobs/shutdown.py` | 43% | Task tracking and graceful shutdown logic |
| `adapters/redis_pool.py` | 35% | Connection pool management; needs fakeredis or import mocking |
| `app/jobs/ticket_stores.py` | 63% | Distributed locking and Redis-backed delivery-ID stores |

### Strategy

- Introduce `fakeredis` fixture for all Redis-dependent modules (redis_queue, redis_pool, ticket_stores, admin, healthz deep checks).
- Add CLI subcommand tests using `monkeypatch` and captured stdout.
- Add `TestClient`-based tests for admin routes and deep health checks.
- Closing top-5 gaps would bring overall coverage from 79% to approximately 87%.

---

## Phase 4: Security

Items to address in the security hardening phase.

### Priority 1 (Address promptly)

1. **Protect `/retry/{ticket_id}` [F-01, HIGH].** Add authentication: either include in `INGEST_PROTECTED_PATHS` for HMAC verification, or require admin bearer token (consistent with `/admin/api/retry/{ticket_id}` which already has auth).

### Priority 2 (Address in next release)

2. **Sanitize deep health check output [F-02, LOW].** Remove storage root path from `?deep=true` response; consider gating deep checks behind authentication or returning boolean pass/fail only.
3. **Warn on unprotected metrics [F-03, LOW].** Log a startup warning when `metrics_enabled=true` but `metrics_bearer_token` is not configured.

### Priority 3 (Consider for future versions)

4. Deprecate HMAC-SHA1 support with a log warning when SHA-1 signatures are used.
5. Add `HEALTHCHECK` instruction to Dockerfile.
6. Add OCI labels to Dockerfile for supply-chain traceability.

---

## Phase 5: Documentation

Items to address in the documentation improvement phase.

### Config Reference Corrections

1. **Add `pdf.templates_root`** to the `pdf` table in `docs/config-reference.md` (default `null`, flat env alias `TEMPLATES_ROOT`).
2. **Move `TSA_USER`/`TSA_PASS`** from section 4 ("Non-schema Runtime Environment Keys") into the `signing.timestamp.rfc3161` table as `user` and `password` fields.

### API Documentation Gaps

3. **Document `POST /admin/api/dlq/replay`** in `docs/api.md` (accepts `limit` query param, default 10, max 1000; returns `{"status":"ok","replayed":<int>}`).
4. **Document `GET /admin/api/config/check`** in `docs/api.md` (returns config validation status with `valid`, `issues`, and `checks` fields).
5. **Document `dry_run` query parameter** on `POST /ingest` and `POST /ingest/batch`.
6. **Document `deep` query parameter** on `GET /healthz` (performs Redis ping, storage write test; may return `"status":"degraded"`).

### Stale References

7. **Update `src/README.md`** to include `routes/jobs.py` and `routes/admin.py`.
8. **Fix `settings.py` comment** on `PdfSettings.template_variant` from `# default|minimal` to `# default|minimal|compact`.

### Polish

9. Add class docstrings to Settings model classes in `settings.py`.
10. Add docstrings to route handler functions (FastAPI uses these as OpenAPI operation descriptions).
11. Add commented-out `TEMPLATES_ROOT` entry to `.env.example` for discoverability.

---

## Dependency Actions (Cross-Phase)

| Priority | Action |
|----------|--------|
| High | Raise pyhanko upper bounds: test 0.34.x compatibility, update to `<0.35`. Same for pyhanko-certvalidator. |
| High | Add `<1.0` upper bounds to fastapi, pydantic, httpx, uvicorn constraints. |
| Medium | Update dev tooling: pytest 9.x, mypy 1.19.x, ruff 0.15.x. |
| Medium | Harden pip-audit failure handling in CI (distinguish tool crash from vuln detection). |
| Medium | Move playwright from `[dev]` to `[scripts]` optional group. |
| Low | Evaluate WeasyPrint/pydyf pin when 69.x releases. |
| Low | Add Dependabot groups for dev dependency PRs. |
| Low | Install pre-commit (declared but missing from local environment). |
