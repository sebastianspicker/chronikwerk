# Static Analysis Baseline

## Ruff Lint Summary

**Total issues: 1**

| Rule Code | Count | Description |
|-----------|-------|-------------|
| E501      | 1     | Line too long |

**Details:**

| File | Line | Message |
|------|------|---------|
| `src/zammad_pdf_archiver/adapters/pdf/render_pdf.py` | 19 | Line too long (113 > 100) |

---

## Complexity (C901) Report

**Total violations: 0**

No functions exceed the configured complexity threshold. All functions in `src/` are within acceptable cognitive complexity limits.

---

## Mypy Type Check Report

**Total errors: 1** (checked 151 source files)

| File | Line | Error Code | Message |
|------|------|------------|---------|
| `src/zammad_pdf_archiver/adapters/signing/tsa_rfc3161.py` | 80 | `arg-type` | Argument "auth" to "post" of "AsyncClient" has incompatible type "tuple[str, str] \| None"; expected "tuple[str \| bytes, str \| bytes] \| Callable[[Request], Request] \| Auth \| UseClientDefault" |

---

## Bugbear Report

**Total issues: 0**

No flake8-bugbear violations detected.

---

## Exception Handling Inventory

**Total `except Exception` occurrences: 58 across 20 files**

### domain/errors.py (1 occurrence)

| Line | Handler Context | Classification | Suggested Tighter Exception |
|------|----------------|----------------|----------------------------|
| 27 | Setting `__cause__` on PermanentError wrapper | INTENTIONAL (fail-safe, best-effort error chaining) | `AttributeError` or `TypeError` |

### domain/html_sanitize.py (1 occurrence)

| Line | Handler Context | Classification | Suggested Tighter Exception |
|------|----------------|----------------|----------------------------|
| 220 | HTML sanitizer parser fail-closed fallback | INTENTIONAL (fail-safe, returns empty string so callers fall back to body_text) | -- |

### domain/audit.py (2 occurrences)

| Line | Handler Context | Classification | Suggested Tighter Exception |
|------|----------------|----------------|----------------------------|
| 23 | `_safe_get_service_version`: metadata.version() lookup | TIGHTENABLE | `PackageNotFoundError` |
| 55 | `_extract_cert_fingerprint`: x509 cert parsing | INTENTIONAL (fail-safe, returns None on any cert parse failure) | -- |

### cli.py (5 occurrences)

| Line | Handler Context | Classification | Suggested Tighter Exception |
|------|----------------|----------------|----------------------------|
| 40 | `cmd_validate_config`: config validation catch-all after FileNotFoundError | TIGHTENABLE | `ValueError, pydantic.ValidationError` |
| 54 | `cmd_dump_config`: config load failure | TIGHTENABLE | `ValueError, pydantic.ValidationError, FileNotFoundError` |
| 89 | `cmd_queue_stats`: queue stats read failure | INTENTIONAL (CLI top-level error handler, prints and exits) | -- |
| 109 | `cmd_queue_drain_dlq`: DLQ drain failure | INTENTIONAL (CLI top-level error handler, prints and exits) | -- |
| 134 | `cmd_queue_history`: queue history read failure | INTENTIONAL (CLI top-level error handler, prints and exits) | -- |

### adapters/redis_pool.py (1 occurrence)

| Line | Handler Context | Classification | Suggested Tighter Exception |
|------|----------------|----------------|----------------------------|
| 81 | `shutdown_redis_pool`: best-effort `aclose()` during shutdown | INTENTIONAL (best-effort cleanup during shutdown) | -- |

### adapters/pdf/template_engine.py (1 occurrence)

| Line | Handler Context | Classification | Suggested Tighter Exception |
|------|----------------|----------------|----------------------------|
| 89 | Jinja2 datetime formatting filter fallback | INTENTIONAL (fail-safe, falls back to str() on any tz/format error) | `KeyError, ValueError, OverflowError` |

### adapters/pdf/url_fetcher.py (1 occurrence)

| Line | Handler Context | Classification | Suggested Tighter Exception |
|------|----------------|----------------|----------------------------|
| 41 | File URL path resolution; re-raises as FatalURLFetchingError | INTENTIONAL (wraps any path error into domain error) | `OSError, ValueError` |

### adapters/snapshot/build_snapshot.py (2 occurrences)

| Line | Handler Context | Classification | Suggested Tighter Exception |
|------|----------------|----------------|----------------------------|
| 83 | HTML-to-text parser fail-closed; returns empty string | INTENTIONAL (fail-safe, same pattern as html_sanitize) | -- |
| 231 | Attachment fetch; returns None on failure | INTENTIONAL (best-effort attachment download, non-fatal) | `httpx.HTTPError, OSError` |

### adapters/signing/tsa_rfc3161.py (1 occurrence)

| Line | Handler Context | Classification | Suggested Tighter Exception |
|------|----------------|----------------|----------------------------|
| 99 | TSA response parse; re-raises as PermanentError (has `noqa: BLE001`) | INTENTIONAL (wraps any ASN.1 parse error into PermanentError) | -- |

### adapters/signing/sign_pdf.py (2 occurrences)

| Line | Handler Context | Classification | Suggested Tighter Exception |
|------|----------------|----------------|----------------------------|
| 86 | PKCS#12 signer init; re-raises as PermanentError (has `noqa: BLE001`) | INTENTIONAL (wraps crypto init error into PermanentError) | -- |
| 114 | PDF signing operation; classifies by exception type for transient vs permanent | INTENTIONAL (process_ticket error handler with type-based classification) | -- |

### adapters/storage/fs_storage.py (3 occurrences)

| Line | Handler Context | Classification | Suggested Tighter Exception |
|------|----------------|----------------|----------------------------|
| 75 | `relative_to()` path traversal check; re-raises as ValueError | TIGHTENABLE | `ValueError` (already the most likely type) |
| 107 | Atomic write cleanup; closes fd and unlinks tmp, then re-raises | INTENTIONAL (best-effort cleanup, re-raises original exception) | -- |
| 126 | `os.replace()` fallback; unlinks tmp, then re-raises | INTENTIONAL (best-effort cleanup, re-raises original exception) | -- |

### adapters/storage/layout.py (1 occurrence)

| Line | Handler Context | Classification | Suggested Tighter Exception |
|------|----------------|----------------|----------------------------|
| 112 | Filename pattern formatting; re-raises as ValueError after catching ValueError separately | TIGHTENABLE | `KeyError, IndexError, TypeError` |

### app/routes/healthz.py (2 occurrences)

| Line | Handler Context | Classification | Suggested Tighter Exception |
|------|----------------|----------------|----------------------------|
| 33 | Redis health check ping; reports `available: False` | INTENTIONAL (health check, must catch any Redis/network error) | -- |
| 42 | Storage write check; reports `writable: False` | INTENTIONAL (health check, must catch any filesystem error) | -- |

### app/routes/admin.py (5 occurrences)

| Line | Handler Context | Classification | Suggested Tighter Exception |
|------|----------------|----------------|----------------------------|
| 62 | Queue stats endpoint; raises HTTPException 503 | INTENTIONAL (Redis fallback, raises HTTPException) | `redis.RedisError, ConnectionError` |
| 81 | History read endpoint; raises HTTPException 503 | INTENTIONAL (Redis fallback, raises HTTPException) | `redis.RedisError, ConnectionError` |
| 102 | Retry dispatch endpoint; raises HTTPException 503 | INTENTIONAL (Redis fallback, raises HTTPException) | `redis.RedisError, ConnectionError` |
| 116 | DLQ drain endpoint; raises HTTPException 503 | INTENTIONAL (Redis fallback, raises HTTPException) | `redis.RedisError, ConnectionError` |
| 132 | DLQ replay endpoint; raises HTTPException 503 | INTENTIONAL (Redis fallback, raises HTTPException) | `redis.RedisError, ConnectionError` |

### app/routes/jobs.py (3 occurrences)

| Line | Handler Context | Classification | Suggested Tighter Exception |
|------|----------------|----------------|----------------------------|
| 39 | Queue stats; returns fallback response on failure | INTENTIONAL (Redis fallback, returns degraded response) | `redis.RedisError, ConnectionError` |
| 65 | History read; raises HTTPException 503 | INTENTIONAL (Redis fallback, raises HTTPException) | `redis.RedisError, ConnectionError` |
| 77 | DLQ drain; raises HTTPException 503 | INTENTIONAL (Redis fallback, raises HTTPException) | `redis.RedisError, ConnectionError` |

### app/routes/ingest.py (1 occurrence)

| Line | Handler Context | Classification | Suggested Tighter Exception |
|------|----------------|----------------|----------------------------|
| 61 | Top-level process_ticket catch-all; logs unhandled error | INTENTIONAL (process_ticket error handler, last-resort logging) | -- |

### app/jobs/history.py (4 occurrences)

| Line | Handler Context | Classification | Suggested Tighter Exception |
|------|----------------|----------------|----------------------------|
| 69 | `record_event`: Redis xadd for history; returns False on failure | INTENTIONAL (Redis fallback, best-effort history recording) | `redis.RedisError, ConnectionError` |
| 79 | `_to_int`: safe int parse helper | TIGHTENABLE | `ValueError, TypeError` |
| 88 | `_to_float`: safe float parse helper | TIGHTENABLE | `ValueError, TypeError` |
| 134 | `read_history`: Redis xrevrange for history; returns empty list | INTENTIONAL (Redis fallback, returns empty on failure) | `redis.RedisError, ConnectionError` |

### app/jobs/redis_queue.py (10 occurrences)

| Line | Handler Context | Classification | Suggested Tighter Exception |
|------|----------------|----------------|----------------------------|
| 71 | Task cancel await in `_stop_one` | INTENTIONAL (best-effort cleanup, suppresses CancelledError) | `asyncio.CancelledError` |
| 91 | Task cancel await in `stop_all` | INTENTIONAL (best-effort cleanup, suppresses CancelledError) | `asyncio.CancelledError` |
| 131 | `_parse_float`: safe float parse helper | TIGHTENABLE | `ValueError, TypeError` |
| 138 | `_parse_int`: safe int parse helper | TIGHTENABLE | `ValueError, TypeError` |
| 268 | `_handle_envelope`: defensive fallback around process_ticket call | INTENTIONAL (redis_queue worker, defensive fallback with `pragma: no cover`) | -- |
| 360 | `_claim_stale_messages`: xpending_range failure | INTENTIONAL (Redis fallback, returns empty list) | `redis.RedisError, ConnectionError` |
| 433 | Worker loop envelope decode failure | INTENTIONAL (redis_queue worker loop, creates error envelope for DLQ) | `json.JSONDecodeError, KeyError, ValueError` |
| 465 | Worker loop handle_envelope failure | INTENTIONAL (redis_queue worker loop, logs and continues) | -- |
| 528 | Worker main loop error (after CancelledError re-raise) | INTENTIONAL (redis_queue worker loop, last-resort catch to keep worker alive) | -- |
| 629 | DLQ replay JSON parse; continues on failure | INTENTIONAL (best-effort DLQ replay, skips unparseable entries) | `json.JSONDecodeError, KeyError, ValueError` |

### app/jobs/process_ticket.py (9 occurrences)

| Line | Handler Context | Classification | Suggested Tighter Exception |
|------|----------------|----------------|----------------------------|
| 105 | History record after ticket processing; logs debug on failure | INTENTIONAL (process_ticket error handler, best-effort history) | -- |
| 120 | `_apply_done_with_backoff`: retries with exponential backoff, re-raises on last attempt | INTENTIONAL (process_ticket error handler, retry loop) | -- |
| 140 | `_apply_done_or_mark_error`: falls back to apply_error on failure | INTENTIONAL (process_ticket error handler, fallback to error state) | -- |
| 268 | Main pipeline exception handler (after CancelledError re-raise) | INTENTIONAL (process_ticket error handler, delegates to `_handle_ticket_pipeline_exception`) | -- |
| 414 | `_safe_apply_done`: logs exception if apply_done_with_backoff fails | INTENTIONAL (process_ticket error handler, best-effort done marking) | -- |
| 517 | Error note creation failed; logs and continues | INTENTIONAL (process_ticket error handler, best-effort error note) | -- |
| 543 | `apply_error` failed during exception handling | INTENTIONAL (process_ticket error handler, best-effort error state marking) | -- |
| 554 | Processing tag cleanup failed | INTENTIONAL (process_ticket error handler, best-effort tag cleanup) | -- |
| 567 | `_release_ticket_lock`: release lock failed | INTENTIONAL (process_ticket error handler, best-effort lock release) | -- |

### app/jobs/ticket_stores.py (3 occurrences)

| Line | Handler Context | Classification | Suggested Tighter Exception |
|------|----------------|----------------|----------------------------|
| 97 | Redis distributed lock acquire failure; falls back to local lock | INTENTIONAL (ticket_stores Redis fallback to local lock) | `redis.RedisError, ConnectionError` |
| 114 | Redis distributed lock release failure | INTENTIONAL (ticket_stores Redis fallback, best-effort release) | `redis.RedisError, ConnectionError` |
| 128 | Redis store aclose during shutdown | INTENTIONAL (best-effort cleanup during shutdown) | -- |

### Summary

| Classification | Count |
|----------------|-------|
| INTENTIONAL    | 48    |
| TIGHTENABLE    | 10    |

**TIGHTENABLE items (10):**

| # | File | Line | Suggested Tighter Exception |
|---|------|------|-----------------------------|
| 1 | `domain/audit.py` | 23 | `PackageNotFoundError` |
| 2 | `cli.py` | 40 | `ValueError, pydantic.ValidationError` |
| 3 | `cli.py` | 54 | `ValueError, pydantic.ValidationError, FileNotFoundError` |
| 4 | `adapters/storage/fs_storage.py` | 75 | `ValueError` |
| 5 | `adapters/storage/layout.py` | 112 | `KeyError, IndexError, TypeError` |
| 6 | `app/jobs/history.py` | 79 | `ValueError, TypeError` |
| 7 | `app/jobs/history.py` | 88 | `ValueError, TypeError` |
| 8 | `app/jobs/redis_queue.py` | 131 | `ValueError, TypeError` |
| 9 | `app/jobs/redis_queue.py` | 138 | `ValueError, TypeError` |
| 10 | `adapters/pdf/template_engine.py` | 89 | `KeyError, ValueError, OverflowError` |

---

## Audit Timestamp

Generated: 2026-03-21

**Tool versions:**
- `ruff check` (output-format=json)
- `mypy` (with pyproject.toml config, 151 source files checked)

**Commands executed:**
1. `python -m ruff check . --output-format=json` -- 1 issue found
2. `python -m ruff check src --select C901 --output-format=json` -- 0 violations
3. `python -m mypy . --config-file pyproject.toml` -- 1 error in 1 file
4. `python -m ruff check . --select B --output-format=json` -- 0 issues
5. `grep -rn "except Exception" src/` -- 58 occurrences across 20 files
