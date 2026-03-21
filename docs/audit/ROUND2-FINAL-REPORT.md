# Round 2 Improvement Process Final Report

## Executive Summary

- **Date:** 2026-03-21
- **Round 2 commits:** 13 (after Round 1 final report `ef9eb06`)
- **Test count:** Round 1 end (355) -> Round 2 end (376) = **+21 tests**
- **Coverage:** Round 1 end (78%) -> Round 2 end (78.96%, effectively 79%)
- **Key achievements:** HIGH security fix (F-01 `/retry` auth), +21 tests, refactored storage module, dependency hardening, docstring and export hygiene

---

## Phase A: Security Fixes

### A.1: `/retry` bearer auth (HIGH fix)

The `POST /retry/{ticket_id}` endpoint previously had no authentication, allowing
unauthenticated callers to trigger ticket re-processing. It now calls the shared
`verify_bearer_auth()` helper from `app.responses` to enforce `Authorization: Bearer <token>`
validation against the configured admin bearer token.

- **3 integration tests added** in `test/integration/test_ingest.py`:
  - `test_retry_requires_auth` -- 401 when no header
  - `test_retry_with_valid_token` -- 202 with correct token
  - `test_retry_with_invalid_token` -- 401 with wrong token
- **Commit:** `7fc38a5`

### A.2: healthz path leak removed

The deep health check (`GET /healthz?deep=true`) previously included a filesystem `path`
key in the storage check response, leaking internal directory structure. The `_check_storage()`
function now returns only `writable` (and optionally `reason`) keys.

- **1 test added** in `test/integration/test_healthz.py`:
  - `test_deep_healthz_does_not_leak_path`
- **Commit:** `948cca6`

### A.3: Metrics startup warning

Enabling `metrics_enabled=True` without configuring a bearer token left the endpoint
unprotected with no operator notification. A `_warn_metrics_without_token()` function
now emits a structured warning during startup.

- **1 test added** in `test/unit/test_config.py`:
  - `test_metrics_without_token_warns`
- **Commit:** `74e5f0e`

---

## Phase B: Test Coverage Push

### B.1: Deep healthz integration tests (4 tests)

- `test_deep_healthz_all_healthy` -- verifies all subsystems report healthy
- `test_deep_healthz_storage_failure` -- verifies degraded response on storage error
- `test_deep_healthz_without_deep_param` -- verifies shallow response without `deep=true`
- `test_deep_healthz_omit_version` -- verifies version omission when configured
- **Commit:** `96a2fb0`

### B.2: Admin endpoint integration tests (5 tests)

- `test_admin_retry_dispatches_job`
- `test_admin_drain_dlq_bounds_limit`
- `test_admin_drain_dlq_returns_503_when_backend_unavailable`
- `test_admin_config_check_returns_results_with_valid_auth`
- `test_admin_config_check_requires_auth`
- **Commit:** `a213920`

### B.3: Batch enforcement tests (2 tests)

- `test_batch_ingest_exceeds_max_size`
- `test_batch_ingest_at_max_size`
- **Commit:** `9d55137`

### B.4: Shutdown module tests (5 tests)

- Full lifecycle and signal-forwarding coverage in `test/unit/test_shutdown.py`
- **Commit:** `9d55137`

---

## Phase C: Code Quality

### C.1: `store_ticket_files` refactored

The monolithic `store_ticket_files` function (127 lines) was decomposed into an
orchestrator plus 3 focused helpers, improving readability and testability.

- **Commit:** `056afa1`

### C.2: Public function docstrings

18 public function docstrings added across remaining undocumented modules.

- **Commit:** `fa985c0`

### C.3: `__all__` exports

Explicit `__all__` exports added to `config/__init__.py` and `domain/errors.py`,
making the public API surface explicit and supporting star-import hygiene.

- **Commit:** `311109f`

---

## Phase D: Dependencies

### D.1: Upper bounds on loose dependencies

Upper bounds added to 10 previously unbounded dependencies to prevent surprise
major-version breaks.

- **Commit:** `94f79ab`

### D.2: Playwright moved to `[scripts]` optional group

`playwright` was in the `[dev]` dependency group but is only used by `scripts/demo/`.
Moved to a dedicated `[scripts]` optional group so `pip install -e ".[dev]"` no longer
pulls in browser binaries.

- **Commit:** `6826a7e`

---

## Cumulative Metrics (Round 1 + Round 2)

| Metric | Before Round 1 | After Round 1 | After Round 2 |
|--------|---------------|---------------|---------------|
| Total improvement commits | 0 | 41 | 54 |
| Test count | 273 | 355 | 376 |
| Coverage | 79% (line-only) | 78% (branch) | 79% (branch) |
| Mypy errors | 1 | 0 | 0 |
| Ruff violations | 1 | 0 | 0 |
| C901 complexity violations | 0 | 0 | 0 |
| Security findings open | 3 | 3 (deferred) | 0 |

- **Security findings resolved:** all 3 -- F-01 HIGH (`/retry` auth), F-02 LOW (healthz path leak), F-03 LOW (metrics warning)
- **All lint / type / complexity checks:** 0 issues
- **Full pipeline (7 steps):** all green

---

## Remaining Items

| Item | Severity | Notes |
|------|----------|-------|
| pyhanko version bump | MEDIUM | Current: 0.27.1, latest: 0.34.1. Requires compatibility testing before bumping upper bound. |
| Lock file for reproducible builds | MEDIUM | Recommended: `uv lock` or `pip-compile` to pin transitive dependencies. |
| Entry-point modules at 0% coverage | LOW | `__main__.py`, `asgi.py`, `runtime.py` are thin wrappers that delegate immediately. |
| `healthz` `all_ok` logic edge case | LOW | `all([]) == True` means an empty checks dict would report healthy even if a subsystem failed to register. |
| Coverage threshold increase | LOW | Currently enforced at 76%; could be raised to 80% given current 79% actual. |

---

## Final Pipeline Verification

| Step | Command | Result |
|------|---------|--------|
| 1. Lint | `python -m ruff check .` | All checks passed |
| 2. Complexity | `python -m ruff check src --select C901` | All checks passed |
| 3. Types | `python -m mypy . --config-file pyproject.toml` | 0 issues in 158 source files |
| 4. Smoke | `bash scripts/ci/smoke-test.sh` | OK |
| 5. Docs | `make docs-check` | OK |
| 6. Tests + Coverage | `pytest --cov --cov-report=term-missing` | 376 passed, 78.96% coverage |
| 7. Build | `python -m build` | zammad_pdf_archiver-0.2.0rc1 (sdist + wheel) |
