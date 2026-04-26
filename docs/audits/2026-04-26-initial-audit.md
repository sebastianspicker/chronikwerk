# Deep Repository Audit (2026-04-26)

## Scope and Objective

This audit reviews how the full repository works end-to-end, with emphasis on:

- feature interactions across ingress, queueing, processing, rendering, storage, and admin APIs
- security controls and failure behavior
- maintainability, duplication, and optimization opportunities
- operational scripts and deployment scaffolding

The goal is to identify and remediate all observed P0/P1/P2 issues in the reviewed code paths.

---

## System Overview: How Everything Works Together

### 1) Ingress and Request Pipeline

- FastAPI app is assembled in `app/server.py` with middleware for request IDs, rate limits, body limits, and HMAC verification.
- Primary entrypoints are `POST /ingest`, `POST /ingest/batch`, and retry/admin APIs.
- Ingest requests are validated, normalized, and dispatched either in-process or via Redis queue.

### 2) Execution Backends

- **In-process backend** executes ticket processing immediately in app runtime.
- **Redis queue backend** writes jobs to stream(s), with worker loop(s) consuming, retrying, and dead-lettering failures.
- Queue status and DLQ actions are available through jobs/admin routes.

### 3) Ticket Processing Pipeline

For each ticket:

1. retrieve ticket and article data from Zammad API adapter
2. build normalized snapshot model
3. render HTML/PDF via Jinja + WeasyPrint
4. optionally sign (PAdES) and timestamp (RFC3161)
5. write PDF + audit sidecar to storage adapter
6. apply note/tag transitions back to Zammad
7. append history event (when enabled)

### 4) Storage and Path Safety

- Layout builder maps archive fields (`archive_path`, user mode) into deterministic directory structures.
- Filesystem adapter validates all write paths are within root and performs atomic write where configured.
- Symlink checks reduce traversal abuse in the target subtree.

### 5) Security and Hardening Controls

- webhook authentication through HMAC header parsing and constant-time digest compare
- optional delivery ID requirement
- request-size limiting and rate limiting at middleware layer
- restricted WeasyPrint URL fetcher (`data:` and constrained `file:` only)
- bearer-token protection for jobs/admin operations

### 6) Scripts and Operations

- `scripts/dev/*`: local run/dev certificate helpers
- `scripts/ci/*`: smoke checks
- `scripts/demo/*`: mock API + screenshot/demo data helpers
- `scripts/ops/*`: CIFS mount helper and PDF verification wrappers

---

## Areas Reviewed

### Application Core

- `src/zammad_pdf_archiver/app/server.py`
- `src/zammad_pdf_archiver/app/routes/{ingest,jobs,admin,healthz}.py`
- `src/zammad_pdf_archiver/app/middleware/{hmac_verify,rate_limit,body_size_limit,request_id}.py`
- `src/zammad_pdf_archiver/app/jobs/*`

### Domain + Adapters

- `domain/{path_policy,idempotency,state_machine,html_sanitize,audit}.py`
- `adapters/{zammad,pdf,storage,signing,snapshot,redis_pool}.py`

### Config + Observability

- `config/{settings,load,validate,redact,env_aliases}.py`
- `observability/{logger,metrics}.py`

### Operational Surface

- `scripts/dev/*`, `scripts/ops/*`, `scripts/ci/*`, `scripts/demo/*`
- deployment materials in `docker-compose*.yml`, `Dockerfile*`, `infra/systemd/*`, docs

---

## Findings (Prioritized)

## P0 (Critical)

- No new unmitigated P0 issue was identified during this pass.

## P1 (High)

### P1-01: Runtime compatibility blocker in retry helper

- **Location:** `app/jobs/async_retry.py`
- **Issue:** PEP 695 generic function syntax (`def fn[T]`) breaks import under Python <3.12, blocking test execution and static checks in mixed contributor/CI environments.
- **Impact:** hard failure during module import/test collection.
- **Remediation:** replaced with `TypeVar`-based generic typing while preserving behavior.
- **Status:** **fixed in this change set**.

## P2 (Medium)

### P2-01: Duplicate bearer auth logic between jobs/admin surfaces

- **Location:** `app/routes/jobs.py` and `app/responses.py`
- **Issue:** duplicated token verification logic increased drift risk.
- **Impact:** inconsistent behavior risk over time; unnecessary maintenance overhead.
- **Remediation:** centralized jobs auth via shared `verify_bearer_auth` helper with configurable missing-token detail.
- **Status:** **fixed in this change set**.

### P2-02: CIFS credentials file hardening gap in ops helper

- **Location:** `scripts/ops/mount-cifs.sh`
- **Issue:** externally supplied credentials file path lacked explicit existence/permission checks.
- **Impact:** accidental weak permissions could expose secrets.
- **Remediation:** added validation requiring existing file and owner-only permission mode (`*00`, e.g. `600`).
- **Status:** **fixed in this change set**.

---

## Refactor / Dedup / Optimization Opportunities (Non-blocking)

1. Consolidate admin/jobs endpoint limit-bounding helpers (`limit` clamp logic appears repeatedly).
2. Add a shared typed result model for queue/admin JSON responses to reduce ad-hoc dict shapes.
3. Expand CI script smoke checks to verify executable bits and basic shellcheck-style invariants.
4. Consider explicit structured error codes for all auth failures to align logs/metrics dashboards.

---

## Validation Commands Run

- `rg --files`
- `sed -n '1,220p' README.md`
- `sed -n '1,240p' src/zammad_pdf_archiver/app/server.py`
- `sed -n '1,280p' src/zammad_pdf_archiver/app/middleware/hmac_verify.py`
- `sed -n '1,260p' src/zammad_pdf_archiver/adapters/storage/fs_storage.py`
- `sed -n '1,260p' src/zammad_pdf_archiver/adapters/pdf/url_fetcher.py`
- `sed -n '1,320p' src/zammad_pdf_archiver/app/routes/admin.py`
- `sed -n '1,260p' src/zammad_pdf_archiver/app/routes/jobs.py`
- `sed -n '1,240p' scripts/ops/mount-cifs.sh`
- `pytest -q test/unit/test_responses.py test/integration/test_jobs_history.py test/unit/test_async_retry.py`

---

## Outcome Summary

- P1 compatibility blocker fixed.
- P2 auth dedup + consistency improvement implemented.
- P2 ops credential hardening improvement implemented.
- Audit artifact upgraded from shallow baseline to deep architecture+risk+remediation coverage.

## Iterative Review Log (20 passes)

1. Entry points and middleware wiring
2. Ingest route validation/dispatch paths
3. Jobs/admin auth and response consistency
4. Queue worker lifecycle and shutdown behavior
5. Retry policy and async retry ergonomics
6. Snapshot build and HTML/text sanitization boundaries
7. PDF rendering fetch policy and local file constraints
8. Signing path error classification and cache behavior
9. Storage path-policy, symlink, atomic-write behavior
10. Audit sidecar model and deterministic fields
11. Config loading, env aliasing, and schema validation
12. Health checks and deep probe degradation logic
13. Metrics/logging redaction and error-shape consistency
14. Dev scripts startup ergonomics and shell safety
15. Ops scripts credential handling and mount guardrails
16. CI scripts assumptions and required path checks
17. Docker/systemd deployment artifacts sanity
18. Unit tests for changed auth/limit helper behavior
19. Python compatibility pass (PEP 695 + UTC imports)
20. Final pass: dedup/refactor opportunities and residual risks
