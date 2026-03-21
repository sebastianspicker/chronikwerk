# Round 2 — Phase B Testing Summary

Date: 2026-03-21

## Baseline (end of Phase A)

| Metric | Value |
|--------|-------|
| Test count (`make test-fast`) | 258 passed |
| Full suite (`pytest`) | 355 passed (round 1 final) |
| Coverage (branch) | 78% |

## Current (end of Phase B)

| Metric | Value |
|--------|-------|
| Test count (full suite) | 376 passed |
| Coverage (branch) | 79% |
| Coverage delta | +1 pp |
| Tests added (since Phase A) | +21 |

## New / modified test files (since Phase A)

| File | Status | Purpose |
|------|--------|---------|
| `test/unit/test_shutdown.py` | Added | Shutdown-handler lifecycle and signal-forwarding tests |
| `test/integration/test_healthz.py` | Modified | Deep health check path-leak regression tests |
| `test/integration/test_ingest.py` | Modified | `/retry` endpoint authentication tests |

## Test commits (Phase B)

| Commit | Message |
|--------|---------|
| `96a2fb0` | test: add deep health check integration tests |
| `9d55137` | test: add batch enforcement and shutdown module tests |

## Verification

```
$ python -m pytest -q --cov=src/zammad_pdf_archiver --cov-report=term
TOTAL  3737  655  1004  193  79%
376 passed, 3 warnings
```
