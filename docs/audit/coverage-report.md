# Test Coverage Report

**Date:** 2026-03-21
**Tool:** pytest-cov 5.x with `--cov=src/zammad_pdf_archiver --cov-report=term-missing`
**Python:** 3.13.5
**Test result:** 273 passed, 1 failed (mypy type-check), 3 warnings

---

## Overall Coverage Summary

| Metric | Value |
|--------|-------|
| Total statements | 3,697 |
| Statements missed | 791 |
| **Overall coverage** | **79%** |
| Files with 100% coverage | 16 |
| Files below 80% coverage | 14 |
| Files with 0% coverage | 3 |

---

## Per-File Coverage Table

Sorted by coverage ascending.

| File | Stmts | Miss | Cover | Missing Lines |
|------|------:|-----:|------:|---------------|
| `__main__.py` | 4 | 4 | 0% | 1-6 |
| `asgi.py` | 7 | 7 | 0% | 1-14 |
| `runtime.py` | 11 | 11 | 0% | 1-25 |
| `adapters/redis_pool.py` | 37 | 24 | 35% | 33-34, 43-47, 56-72, 77-83 |
| `cli.py` | 117 | 74 | 37% | 29-42, 47-56, 61-79, 89-91, 109-111, 120-122, 134-136, 141-210, 214 |
| `app/jobs/shutdown.py` | 28 | 16 | 43% | 13, 18, 23, 29-42 |
| `app/routes/healthz.py` | 48 | 25 | 48% | 19-20, 24-34, 38-43, 58-68 |
| `app/jobs/redis_queue.py` | 371 | 175 | 53% | 37-53, 58-75, 77-95, 101-102, ... (extensive) |
| `app/jobs/ticket_path.py` | 44 | 17 | 61% | 8, 11, 30-46, 51, 60, 65, 68 |
| `app/jobs/ticket_stores.py` | 90 | 33 | 63% | 28-33, 40, 43, 61, 68, 71, 90-99, 112-115, 124-134 |
| `app/routes/admin.py` | 100 | 32 | 68% | 35, 40, 53, 62-64, 81-83, 102-104, 126-137, 142-160 |
| `adapters/pdf/url_fetcher.py` | 34 | 9 | 74% | 25, 29, 34, 38-42, 45 |
| `adapters/pdf/template_engine.py` | 59 | 14 | 76% | 30, 33, 35, 37, 61-66, 71, 84, 89-90 |
| `domain/errors.py` | 13 | 3 | 77% | 21, 27-28 |
| `domain/redis_delivery_id.py` | 30 | 7 | 77% | 23, 29, 45-48, 52-53 |
| `app/server.py` | 57 | 13 | 77% | 33-45 |
| `_version.py` | 9 | 2 | 78% | 9-10 |
| `adapters/storage/layout.py` | 72 | 14 | 81% | 17, 22, 42, 55, 94, 105-113, 117, 121, 125 |
| `domain/audit.py` | 59 | 11 | 81% | 44, 47-57 |
| `app/jobs/ticket_notes.py` | 60 | 11 | 82% | 56, 58, 60, 62, 64, 69, 71, 73, 129, 131, 136 |
| `app/jobs/ticket_storage.py` | 67 | 13 | 81% | 119-134, 175-178 |
| `app/middleware/rate_limit.py` | 104 | 18 | 83% | 48-61, 86, 99-100, 133-134, 142-143 |
| `adapters/signing/sign_pdf.py` | 73 | 11 | 85% | 28, 49, 56, 60, 70, 86-87, 114-124 |
| `app/middleware/body_size_limit.py` | 55 | 8 | 85% | 32, 35-37, 47, 52, 80-82 |
| `adapters/snapshot/build_snapshot.py` | 154 | 18 | 88% | 53-54, 60-61, 83-84, 118, 157, 217, 221, 229, 231-232, 237, 274, 311, 314, 316 |
| `adapters/storage/fs_storage.py` | 104 | 12 | 88% | 23-24, 27-28, 83-86, 134-137, 142 |
| `adapters/signing/tsa_rfc3161.py` | 60 | 7 | 88% | 30, 42-44, 66, 99-100 |
| `domain/html_sanitize.py` | 119 | 13 | 89% | 76, 108, 140, 150, 160, 172, 174, 177, 193-194, 213, 220-222 |
| `app/routes/jobs.py` | 54 | 6 | 89% | 29, 36, 39-40, 65-66 |
| `app/responses.py` | 18 | 2 | 89% | 15, 33 |
| `domain/time_utils.py` | 9 | 1 | 89% | 12 |
| `app/routes/ingest.py` | 100 | 10 | 90% | 51-52, 62, 73, 76, 116, 120, 148, 151, 180 |
| `config/validate.py` | 90 | 9 | 90% | 43, 168-169, 188-201 |
| `domain/ticket_utils.py` | 10 | 1 | 90% | 17 |
| `adapters/pdf/render_pdf.py` | 69 | 6 | 91% | 33, 38, 59-60, 67, 97 |
| `config/redact.py` | 53 | 4 | 92% | 96, 102, 108, 110 |
| `domain/idempotency.py` | 53 | 4 | 92% | 27, 52-53, 74 |
| `domain/ticket_id.py` | 26 | 2 | 92% | 16, 18 |
| `domain/error_messages.py` | 27 | 2 | 93% | 32, 42 |
| `adapters/http_util.py` | 14 | 1 | 93% | 24 |
| `app/middleware/hmac_verify.py` | 124 | 7 | 94% | 67, 96, 113, 138-139, 177-178 |
| `config/load.py` | 71 | 4 | 94% | 40, 48-49, 54 |
| `app/jobs/ticket_renderer.py` | 32 | 2 | 94% | 55-61 |
| `config/settings.py` | 176 | 9 | 95% | 56, 60, 164, 184-189 |
| `observability/logger.py` | 52 | 2 | 96% | 22, 29 |
| `config/env_aliases.py` | 39 | 1 | 97% | 60 |
| `domain/path_policy.py` | 57 | 1 | 98% | 53 |
| `app/jobs/retry_policy.py` | 42 | 1 | 98% | 59 |
| `__init__.py` | 3 | 0 | 100% | — |
| `adapters/__init__.py` | 1 | 0 | 100% | — |
| `adapters/pdf/__init__.py` | 0 | 0 | 100% | — |
| `adapters/signing/__init__.py` | 1 | 0 | 100% | — |
| `adapters/snapshot/__init__.py` | 1 | 0 | 100% | — |
| `adapters/storage/__init__.py` | 3 | 0 | 100% | — |
| `app/__init__.py` | 1 | 0 | 100% | — |
| `app/middleware/__init__.py` | 1 | 0 | 100% | — |
| `app/middleware/request_id.py` | 23 | 0 | 100% | — |
| `app/routes/__init__.py` | 1 | 0 | 100% | — |
| `app/routes/metrics.py` | 23 | 0 | 100% | — |
| `app/jobs/ticket_fetcher.py` | 13 | 0 | 100% | — |
| `config/__init__.py` | 0 | 0 | 100% | — |
| `domain/__init__.py` | 1 | 0 | 100% | — |
| `domain/snapshot_models.py` | 40 | 0 | 100% | — |
| `domain/state_machine.py` | 32 | 0 | 100% | — |
| `observability/__init__.py` | 1 | 0 | 100% | — |
| `observability/metrics.py` | 15 | 0 | 100% | — |

---

## Files Below 80% Coverage (with Analysis)

### 1. `__main__.py` — 0% (4 stmts)

Entry-point module (`python -m zammad_pdf_archiver`). Imports `runtime.main` and calls it under `if __name__ == "__main__"`. Never executed during tests because tests import modules directly rather than running the package as a script.

### 2. `asgi.py` — 0% (7 stmts)

Module-level ASGI application factory. Calls `load_settings()`, `configure_logging()`, and `create_app()` at import time. Untested because importing it triggers side effects (settings loading) that conflict with test harness fixtures.

### 3. `runtime.py` — 0% (11 stmts)

Production entry point: loads settings, configures logging, and starts `uvicorn.run()`. Cannot be tested without starting a real server. All constituent functions (`load_settings`, `configure_logging`, `create_app`) are tested individually elsewhere.

### 4. `adapters/redis_pool.py` — 35% (37 stmts, 24 missed)

Redis connection pool with lazy imports. Uncovered lines include:
- `import_redis_class()` function (lines 43-47): alternative import path returning `None` when redis not installed
- `get_redis()` (lines 56-72): async client creation and caching with URL validation
- `close_all()` (lines 77-83): graceful shutdown of all cached clients

These require a live Redis instance or thorough mocking of the `redis.asyncio` module.

### 5. `cli.py` — 37% (117 stmts, 74 missed)

CLI entry point with 6 subcommands. Covered: parser construction and `cmd_validate_config` success path. Uncovered:
- `cmd_validate_config` error paths (FileNotFoundError, generic Exception)
- `cmd_dump_config` (lines 47-56): config dump with redaction
- `cmd_show_deprecated` (lines 61-79): deprecated env var reporting
- `cmd_queue_stats` (lines 82-91): queue stats display
- `cmd_queue_drain_dlq` (lines 94-111): DLQ drain command
- `cmd_queue_history` (lines 114-136): history display
- `main()` dispatch logic (lines 141-210)

### 6. `app/jobs/shutdown.py` — 43% (28 stmts, 16 missed)

Graceful shutdown coordination. Covered: `is_shutting_down()`, `set_shutting_down()`. Uncovered:
- `clear_shutting_down()` (line 18): reset global flag
- `track_task()` (lines 23-25): register asyncio tasks for shutdown
- `wait_for_tasks()` (lines 29-42): await pending tasks with timeout and cancellation

### 7. `app/routes/healthz.py` — 48% (48 stmts, 25 missed)

Health check endpoint. Covered: basic `/healthz` response. Uncovered:
- `_service_version()` (lines 19-20): PackageNotFoundError fallback
- `_check_redis()` (lines 24-34): deep health check — Redis ping
- `_check_storage()` (lines 38-43): deep health check — storage writability test
- Deep health check logic in `healthz()` (lines 58-68): aggregating sub-check results and setting "degraded" status

### 8. `app/jobs/redis_queue.py` — 53% (371 stmts, 175 missed)

The Redis Streams-based job queue. This is the largest file in the codebase and the biggest coverage gap. Uncovered areas:
- `RedisQueueManager` class (lines 37-95): worker lifecycle (start, stop, stop_all)
- `_worker_loop()` (lines 475-530): main consumer loop with claim-stale, read-pending, read-new phases
- `_claim_stale_pending()` (lines 349-380): reclaim stuck messages from other consumers
- `_read_own_pending()` / `_read_new_messages()` (lines 383-417): stream reads
- `get_queue_stats()` (lines 552-582): operational queue depth introspection
- `drain_dlq()` / `replay_dlq()` (lines 585-642): DLQ management
- `aclose_queue_clients()` (lines 645-648): connection teardown

All require a live Redis instance or extensive mocking of `redis.asyncio` stream operations.

### 9. `app/jobs/ticket_path.py` — 61% (44 stmts, 17 missed)

Archive path computation. Uncovered:
- `determine_username()` with `mode="current_agent"` (lines 30-38): extracting login from payload or `updated_by`
- `determine_username()` with `mode="fixed"` (lines 40-44): using a custom field
- `determine_username()` with unsupported mode (line 46): ValueError
- `parse_archive_path_segments()` with list input (lines 56-63): list-of-strings parsing

### 10. `app/jobs/ticket_stores.py` — 63% (90 stmts, 33 missed)

Delivery-ID and ticket-lock store management. Uncovered:
- `_get_redis_store()` (lines 28-33): Redis store deduplication cache
- `_get_delivery_id_store()` Redis backend path (line 43)
- `_get_ticket_lock_store()` (lines 65-76): distributed ticket lock initialization
- `try_acquire_ticket()` distributed lock path (lines 90-99): Redis-backed cross-process locks
- `release_ticket()` Redis path (lines 112-115): distributed lock release
- `aclose_stores()` (lines 124-134): cleanup of all stores

### 11. `app/routes/admin.py` — 68% (100 stmts, 32 missed)

Admin API routes. Covered: `_verify_admin_auth()`. Uncovered:
- `admin_dashboard()` GET (line 53): serves HTML dashboard
- `admin_queue_stats()` (lines 62-64): queue stats endpoint body
- `admin_history()` (lines 81-83): history endpoint body
- `admin_retry_ticket()` (lines 102-104): retry dispatch endpoint
- `admin_drain_dlq()` / `admin_replay_dlq()` (lines 126-137): DLQ management endpoints
- `admin_config_check()` (lines 142-160): runtime config validation endpoint

### 12. `adapters/pdf/url_fetcher.py` — 74% (34 stmts, 9 missed)

Safe URL fetcher for WeasyPrint. Uncovered:
- `data:` URL delegation (line 25)
- Relative file path resolution (line 29)
- Error handling for non-file paths (lines 38-42): invalid file URL exceptions
- Blocked scheme fallback (line 45)

### 13. `adapters/pdf/template_engine.py` — 76% (59 stmts, 14 missed)

Jinja2 template engine for PDF rendering. Uncovered:
- Input validation branches in `validate_template_name()` (lines 30, 33, 35, 37)
- `_loader_for()` with custom `templates_root` (lines 61-66): FileSystemLoader path
- `_register_filters()` inner functions (lines 71, 84, 89-90): datetime formatting filters

### 14. `domain/errors.py` — 77% (13 stmts, 3 missed)

Error hierarchy. Uncovered: `wrap_exception()` when exception is already a domain type (line 21), and the `__cause__` assignment (lines 27-28).

---

## Uncovered Modules

Three modules have **zero** test coverage:

| Module | Purpose | Why untested |
|--------|---------|--------------|
| `__main__.py` | `python -m` entry point | Script guard; delegates to `runtime.main()` |
| `asgi.py` | ASGI app factory for uvicorn | Side effects at import time (settings load) |
| `runtime.py` | Production server bootstrap | Calls `uvicorn.run()` which blocks |

These are thin wrappers around tested components. They could be covered with subprocess-based or mock-patched tests.

---

## Specifically Requested File Coverage

| File | Coverage | Status |
|------|----------|--------|
| `app/jobs/redis_queue.py` | 53% | Below threshold — largest gap (175 missed stmts) |
| `adapters/signing/sign_pdf.py` | 85% | Above threshold |
| `adapters/signing/tsa_rfc3161.py` | 88% | Above threshold |
| `app/routes/admin.py` | 68% | Below threshold — admin endpoints untested |
| `cli.py` | 37% | Below threshold — most subcommands untested |
| `adapters/pdf/url_fetcher.py` | 74% | Below threshold |

---

## Test Suite Composition

| Category | Test Files | Description |
|----------|----------:|-------------|
| `test/unit/` | 39 | Unit tests — isolated, fast, no external deps |
| `test/integration/` | 18 | Integration tests — FastAPI TestClient, mocked services |
| `test/nfr/` | 10 | Non-functional requirements — security, deployment, docs |
| `test/static/` | 1 | Static analysis (mypy type checking) |
| **Total** | **68** | |

Total test count: **274** (273 passed, 1 failed — mypy found a type error in `tsa_rfc3161.py:80`).

---

## Test Gap Analysis

### Critical gaps (high-value targets for new tests)

1. **`redis_queue.py` (53%)** — The entire Redis Streams worker loop, message claiming, DLQ drain/replay, and queue stats are untested. This is the most complex module in the codebase and handles job reliability. Requires either a Redis test fixture (e.g., `fakeredis` or testcontainers) or targeted mocking of `redis.asyncio`.

2. **`cli.py` (37%)** — Five of six CLI subcommands have no test coverage. These are pure functions that call `load_settings()` and print JSON, making them straightforward to test with `monkeypatch` and captured stdout.

3. **`admin.py` (68%)** — Admin API endpoints for queue management, history, retry, and config checking. Can be tested via FastAPI `TestClient` with mocked Redis.

4. **`healthz.py` (48%)** — Deep health checks (Redis ping, storage writability probe) are untested. The shallow `/healthz` path works, but `?deep=true` is not exercised.

### Moderate gaps

5. **`shutdown.py` (43%)** — Task tracking and graceful shutdown logic. Testable with `asyncio` test utilities.

6. **`redis_pool.py` (35%)** — Connection pool management. Most of the module requires `redis.asyncio`; `fakeredis` or import mocking would close this gap.

7. **`ticket_stores.py` (63%)** — Distributed locking and Redis-backed delivery-ID stores. Same Redis dependency as above.

8. **`ticket_path.py` (61%)** — Pure logic for archive path computation. The `current_agent` and `fixed` modes and list-input parsing are untested — easy to add.

### Low-effort improvements

9. **`url_fetcher.py` (74%)** — Additional test cases for `data:` URLs, relative paths, and blocked schemes.

10. **`template_engine.py` (76%)** — Validation edge cases and custom `templates_root` path.

11. **`domain/errors.py` (77%)** — `wrap_exception()` with already-wrapped exceptions.

---

## Recommended Coverage Threshold

| Tier | Threshold | Rationale |
|------|-----------|-----------|
| **Domain logic** (`domain/`) | 95% | Pure functions, no I/O, easy to test exhaustively |
| **Adapters** (`adapters/`) | 85% | External integration boundaries; mock where needed |
| **Application layer** (`app/`) | 85% | Routes and middleware; use TestClient |
| **Entry points** (`cli.py`, `runtime.py`, `asgi.py`, `__main__.py`) | 60% | Thin wrappers; diminishing returns |
| **Overall project minimum** | 85% | Current: 79% — a 6-point gap to close |

### Priority actions to reach 85%

1. Add `fakeredis`-based tests for `redis_queue.py` (+175 stmts, ~5% overall gain)
2. Add CLI subcommand tests for `cli.py` (+74 stmts, ~2% overall gain)
3. Add deep health check tests for `healthz.py` (+25 stmts, ~0.7% gain)
4. Add admin route tests for `admin.py` (+32 stmts, ~0.9% gain)
5. Add `ticket_path.py` mode tests (+17 stmts, ~0.5% gain)

These five items would bring overall coverage from 79% to approximately 87%.
