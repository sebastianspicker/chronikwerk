# Round 2 — Phase A Security Summary

Date: 2026-03-21

## A.1: `/retry` endpoint auth fix

**Issue:** The `POST /retry/{ticket_id}` endpoint lacked authentication, allowing
unauthenticated callers to trigger ticket re-processing.

**Fix:** The endpoint now calls the shared `verify_bearer_auth()` helper
(from `zammad_pdf_archiver.app.responses`) to enforce `Authorization: Bearer <token>`
validation against the configured admin bearer token.

**Tests added (3):**

| Test | File | Purpose |
|------|------|---------|
| `test_retry_requires_auth` | `test/integration/test_ingest.py` | 401 when no header |
| `test_retry_with_valid_token` | `test/integration/test_ingest.py` | 202 with correct token |
| `test_retry_with_invalid_token` | `test/integration/test_ingest.py` | 401 with wrong token |

**Key files:**

- `src/zammad_pdf_archiver/app/routes/ingest.py` — calls `verify_bearer_auth`
- `src/zammad_pdf_archiver/app/responses.py` — shared auth helper

## A.2: Health check path leak removed

**Issue:** The deep health check (`GET /healthz?deep=true`) previously included the
filesystem `path` key in the storage check response, leaking internal directory
structure to callers.

**Fix:** `_check_storage()` in `healthz.py` now returns only `{"writable": True}` on
success or `{"writable": False, "reason": "<error>"}` on failure. No `path` key is
present in either case.

**Tests added (1):**

| Test | File | Purpose |
|------|------|---------|
| `test_deep_healthz_does_not_leak_path` | `test/integration/test_healthz.py` | Asserts no `path` key and no filesystem path in response body |

**Key file:** `src/zammad_pdf_archiver/app/routes/healthz.py`

## A.3: Metrics startup warning

**Issue:** Enabling the metrics endpoint (`metrics_enabled=True`) without configuring
a bearer token left the endpoint unprotected, with no indication to the operator.

**Fix:** `_warn_metrics_without_token()` in `validate.py` emits a structured warning
during startup when metrics are enabled but no bearer token is configured.

**Tests added (1):**

| Test | File | Purpose |
|------|------|---------|
| `test_metrics_without_token_warns` | `test/unit/test_config.py` | Asserts warning message is emitted |

**Key file:** `src/zammad_pdf_archiver/config/validate.py`

## Verification results

| Check | Result |
|-------|--------|
| `verify_bearer_auth` in `/retry` route | Confirmed (line 193 of `ingest.py`) |
| No `path` key in `_check_storage` success dict | Confirmed (`healthz.py` returns only `writable` key) |
| `_warn_metrics_without_token` called during validation | Confirmed (line 90 of `validate.py`) |
| `make test-fast` | 258 passed, 0 failed |
