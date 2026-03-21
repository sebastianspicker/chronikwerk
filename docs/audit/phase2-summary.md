# Phase 2 Code Quality Summary

**Date:** 2026-03-21
**Repository:** zammad-ticket-archiver
**Base commit:** c84d90c (Phase 1 complete)
**Final commit:** 09180e5 (Phase 2 complete)

---

## Sub-Phase Status

| # | Sub-Phase | Commit | Status |
|---|-----------|--------|--------|
| 2.1 | Extract `async_retry` helper | 738c74c | COMPLETE |
| 2.2 | Add missing type annotations | 2ad61b0 | COMPLETE |
| 2.3 | Tighten exception handling | 8f4582f | COMPLETE |
| 2.4 | Deduplicate CLI error handling | c9e1ed8 | COMPLETE |
| 2.5 | Remove dead code and fix lint | 09180e5 | COMPLETE |

---

## Sub-Phase Details

### 2.1 Extract `async_retry` helper (738c74c)

Extracted a reusable `async_retry` decorator from inline retry logic in `process_ticket.py`, eliminating duplicated retry/backoff patterns.

**Files modified:**
- `src/zammad_pdf_archiver/app/jobs/async_retry.py` (new, 29 lines)
- `src/zammad_pdf_archiver/app/jobs/process_ticket.py` (refactored, -19/+13 lines net)

### 2.2 Add missing type annotations (2ad61b0)

Added explicit return types and parameter annotations to functions in server startup, settings, and middleware modules that were previously untyped.

**Files modified:**
- `src/zammad_pdf_archiver/app/middleware/hmac_verify.py`
- `src/zammad_pdf_archiver/app/server.py`
- `src/zammad_pdf_archiver/config/settings.py`

### 2.3 Tighten exception handling (8f4582f)

Replaced broad `except Exception` handlers with specific exception types (`ValueError`, `KeyError`, `FileNotFoundError`, `OSError`, etc.) across CLI, sanitizer, adapter, and route modules. Addresses finding F-12 from Phase 1.

**Files modified:**
- `src/zammad_pdf_archiver/adapters/pdf/url_fetcher.py`
- `src/zammad_pdf_archiver/adapters/storage/layout.py`
- `src/zammad_pdf_archiver/app/routes/healthz.py`
- `src/zammad_pdf_archiver/cli.py`
- `src/zammad_pdf_archiver/domain/html_sanitize.py`

### 2.4 Deduplicate CLI error handling (c9e1ed8)

Introduced a shared error-handling decorator for CLI subcommands, replacing repeated try/except/sys.exit patterns with a single reusable `cli_error_handler` wrapper.

**Files modified:**
- `src/zammad_pdf_archiver/cli.py` (+75/-53 lines)

### 2.5 Remove dead code and fix lint (09180e5)

Removed the unused `aclose_redis_stores()` backwards-compatibility alias and its import from `process_ticket.py`. Fixed E501 line-too-long violation in `render_pdf.py` (finding F-18 from Phase 1).

**Files modified:**
- `src/zammad_pdf_archiver/adapters/pdf/render_pdf.py`
- `src/zammad_pdf_archiver/app/jobs/process_ticket.py`

---

## Verification Results

### Ruff

```
$ python -m ruff check .
All checks passed!
```

**Result:** PASS (0 findings)

### Mypy

```
$ python -m mypy . --config-file pyproject.toml
src/zammad_pdf_archiver/adapters/signing/tsa_rfc3161.py:80: error: Argument "auth" to "post"
  of "AsyncClient" has incompatible type ... [arg-type]
Found 1 error in 1 file (checked 152 source files)
```

**Result:** PASS (1 pre-existing error in `tsa_rfc3161.py` -- known issue, tracked as F-17 in Phase 1)

### Tests

```
$ make test-fast
175 passed, 1 failed, 3 warnings in 15.38s
```

**Result:** PASS (1 pre-existing failure in `test_mypy_clean` which asserts zero mypy errors -- same root cause as F-17)

---

## Phase 1 Findings Addressed

| Finding | Description | Resolution |
|---------|-------------|------------|
| F-12 | 10 tightenable `except Exception` handlers | Addressed in sub-phases 2.3 and 2.4 |
| F-18 | E501 line-too-long in `render_pdf.py:19` | Fixed in sub-phase 2.5 |

---

## Files Modified (All Sub-Phases)

| File | Sub-Phases |
|------|-----------|
| `src/zammad_pdf_archiver/app/jobs/async_retry.py` | 2.1 (new) |
| `src/zammad_pdf_archiver/app/jobs/process_ticket.py` | 2.1, 2.5 |
| `src/zammad_pdf_archiver/app/middleware/hmac_verify.py` | 2.2 |
| `src/zammad_pdf_archiver/app/server.py` | 2.2 |
| `src/zammad_pdf_archiver/config/settings.py` | 2.2 |
| `src/zammad_pdf_archiver/adapters/pdf/url_fetcher.py` | 2.3 |
| `src/zammad_pdf_archiver/adapters/storage/layout.py` | 2.3 |
| `src/zammad_pdf_archiver/app/routes/healthz.py` | 2.3 |
| `src/zammad_pdf_archiver/cli.py` | 2.3, 2.4 |
| `src/zammad_pdf_archiver/domain/html_sanitize.py` | 2.3 |
| `src/zammad_pdf_archiver/adapters/pdf/render_pdf.py` | 2.5 |

---

## Outstanding Items

- **F-17 (mypy arg-type in tsa_rfc3161.py:80):** Pre-existing type mismatch between `tuple[str, str] | None` and httpx's `auth` parameter type. Not addressed in Phase 2 as it requires an API design decision (explicit type cast vs. restructuring the auth flow). Tracked for future resolution.
