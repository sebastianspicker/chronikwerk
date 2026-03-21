# Documentation Completeness Audit

**Date:** 2026-03-21
**Scope:** All documentation files, config reference, API docs, source code docstrings.
**Method:** Manual cross-check of every documentation file against source code in `src/zammad_pdf_archiver/`.

---

## 1. Documentation Inventory

| File | Lines | Purpose |
|------|------:|---------|
| `README.md` | 290 | Project overview, quickstart, endpoint list, config overview, glossary |
| `CHANGELOG.md` | 35 | Release history (0.1.0, 0.2.0-rc.1) |
| `CONTRIBUTING.md` | 41 | Contributor workflow and release process |
| `SECURITY.md` | 17 | Vulnerability reporting policy |
| `CODE_OF_CONDUCT.md` | 115 | Contributor Covenant |
| `src/README.md` | 25 | Source directory layout guide |
| `docs/PRD.md` | 273 | Product Requirements Document |
| `docs/01-architecture.md` | 170 | Architecture overview, sequence diagrams, module boundaries |
| `docs/02-zammad-setup.md` | 146 | Zammad admin setup guide |
| `docs/03-data-model.md` | 54 | Snapshot and audit sidecar data model |
| `docs/04-path-policy.md` | 113 | Path parsing, validation, sanitization, containment |
| `docs/05-pdf-rendering.md` | 124 | Rendering pipeline, template variants, HTML safety |
| `docs/06-signing-and-timestamp.md` | 136 | PAdES signing and RFC3161 timestamping |
| `docs/07-storage.md` | 97 | File output, permissions, atomic writes, CIFS/SMB |
| `docs/08-operations.md` | 235 | Operational runbook, troubleshooting matrix, scripts |
| `docs/09-security.md` | 112 | Threat model, mitigations, residual risks, hardening checklist |
| `docs/api.md` | 281 | HTTP API contract, webhook security, idempotency |
| `docs/config-reference.md` | 229 | Full configuration key reference |
| `docs/deploy.md` | 140 | Production deployment guide |
| `docs/faq.md` | 97 | Frequently asked questions |
| `docs/release-checklist.md` | 175 | Release and deployment checklist |
| `docs/demo-mock-university.md` | 201 | Local demo stack tutorial |
| `docs/adr/0001-tag-vs-fields.md` | 46 | ADR: trigger via tag |
| `docs/adr/0002-storage-approach.md` | 34 | ADR: storage approach |
| `docs/adr/0003-signature-timestamp.md` | 43 | ADR: signing + timestamping |
| **Total** | **3229** | |

---

## 2. Config Reference Accuracy

Source of truth: `src/zammad_pdf_archiver/config/settings.py`
Doc under review: `docs/config-reference.md`

### 2.1 Missing from config-reference.md

| Settings field | Type | Default | Notes |
|---|---|---|---|
| `pdf.templates_root` | `Path \| None` | `None` | Present in `PdfSettings` (line 108) and has flat env alias `TEMPLATES_ROOT` in `env_aliases.py` (line 103). Documented in `docs/05-pdf-rendering.md` but **missing from the `pdf` table** in `config-reference.md`. |

### 2.2 Inaccuracy: TSA_USER / TSA_PASS described as "non-schema"

`docs/config-reference.md` section 4 ("Non-schema Runtime Environment Keys") lists `TSA_USER` and `TSA_PASS` as "used by runtime/deployment but not part of `Settings` model." This is **inaccurate**: `settings.py` defines `user: str | None = None` and `password: SecretStr | None = None` on `SigningTimestampRfc3161Settings` (lines 136-137), and `env_aliases.py` maps `TSA_USER` to `("signing", "timestamp", "rfc3161", "user")` and `TSA_PASS` to `("signing", "timestamp", "rfc3161", "password")` (lines 124-125). These are schema fields with flat env aliases, not non-schema keys.

### 2.3 Missing from signing.timestamp.rfc3161 table

The `signing.timestamp.rfc3161` table in config-reference.md does not include rows for:

| Key | Default | Flat env alias | Description |
|---|---|---|---|
| `signing.timestamp.rfc3161.user` | `null` | `TSA_USER` | TSA HTTP basic auth username |
| `signing.timestamp.rfc3161.password` | `null` | `TSA_PASS` | TSA HTTP basic auth password |

These should be moved out of section 4 and into the `signing.timestamp.rfc3161` table.

### 2.4 Minor: PdfSettings template_variant comment says "default|minimal"

In `settings.py` line 107, the inline comment reads `# default|minimal`, but the `compact` variant also exists (as templates and docs confirm). The comment should read `# default|minimal|compact`. This does not affect runtime behavior since the comment is not enforced.

### 2.5 All other fields: MATCH

All remaining fields in settings.py are accurately documented in config-reference.md with correct defaults, types, and flat env alias names. Specifically verified:

- `server.*` (3 fields) -- correct
- `zammad.*` (5 fields) -- correct
- `workflow.*` (16 fields) -- correct
- `fields.*` (3 fields) -- correct
- `storage.*` and `storage.path_policy.*` (7 fields) -- correct
- `pdf.*` (7 of 8 fields; `templates_root` missing as noted above) -- correct for documented fields
- `signing.*` (3 fields), `signing.pades.*` (5 fields), `signing.timestamp.*` + `rfc3161.*` (4 of 6; user/password missing as noted above) -- correct for documented fields
- `observability.*` (6 fields) -- correct
- `hardening.rate_limit.*` (5 fields) -- correct
- `hardening.body_size_limit.*` (1 field) -- correct
- `hardening.webhook.*` (3 fields) -- correct
- `hardening.transport.*` (4 fields) -- correct
- `admin.*` (3 fields) -- correct

---

## 3. API Documentation Accuracy

Source of truth: route files in `src/zammad_pdf_archiver/app/routes/`
Doc under review: `docs/api.md`

### 3.1 Undocumented endpoints

The following routes exist in code but are **not documented** in `docs/api.md`:

| Endpoint | File | Notes |
|---|---|---|
| `POST /admin/api/dlq/replay` | `routes/admin.py:122` | Replays DLQ entries back to the main queue. Accepts `limit` query param (default 10, max 1000). Returns `{"status":"ok","replayed":<int>}`. |
| `GET /admin/api/config/check` | `routes/admin.py:140` | Returns config validation status with `valid`, `issues`, and `checks` fields. |

Both require `admin.enabled=true` and Bearer token.

### 3.2 Undocumented query parameters

| Endpoint | Parameter | Source | Notes |
|---|---|---|---|
| `POST /ingest` | `dry_run: bool = False` | `routes/ingest.py:112` | When `true`, returns `202` with `{"status":"dry_run_accepted",...}` without dispatching work. Not documented in `docs/api.md`. |
| `POST /ingest/batch` | `dry_run: bool = False` | `routes/ingest.py:144` | Same as above for batch endpoint. |
| `GET /healthz` | `deep: bool = False` | `routes/healthz.py:47` | When `true`, performs deep checks (Redis ping, storage write test) and returns a `checks` object. May return `"status":"degraded"`. Not documented in `docs/api.md`. |

### 3.3 Endpoint listed in README but not in api.md admin section

`README.md` lists `GET /admin/api/*` as a wildcard. `docs/api.md` section "Admin API" lists 4 admin endpoints but is missing:
- `POST /admin/api/dlq/replay`
- `GET /admin/api/config/check`

### 3.4 All other endpoints: MATCH

All other endpoints documented in `docs/api.md` match their code implementations:

- `POST /ingest` -- matches `routes/ingest.py` (status codes, response shapes, error codes)
- `POST /ingest/batch` -- matches `routes/ingest.py`
- `POST /retry/{ticket_id}` -- matches `routes/ingest.py`
- `GET /jobs/{ticket_id}` -- matches `routes/jobs.py`
- `GET /jobs/queue/stats` -- matches `routes/jobs.py`
- `GET /jobs/history` -- matches `routes/jobs.py`
- `POST /jobs/queue/dlq/drain` -- matches `routes/jobs.py`
- `GET /healthz` -- matches `routes/healthz.py` (basic behavior; deep mode undocumented)
- `GET /metrics` -- matches `routes/metrics.py`
- `GET /admin` -- matches `routes/admin.py`
- `GET /admin/api/queue/stats` -- matches `routes/admin.py`
- `GET /admin/api/history` -- matches `routes/admin.py`
- `POST /admin/api/retry/{ticket_id}` -- matches `routes/admin.py`
- `POST /admin/api/dlq/drain` -- matches `routes/admin.py`

---

## 4. Architecture Doc Alignment

### 4.1 File paths referenced in docs

All file paths referenced in documentation were verified to exist:

| Reference | Exists |
|---|---|
| `src/zammad_pdf_archiver/config/settings.py` | Yes |
| `src/zammad_pdf_archiver/config/load.py` | Yes |
| `src/zammad_pdf_archiver/config/validate.py` | Yes |
| `src/zammad_pdf_archiver/app/server.py` | Yes |
| `src/zammad_pdf_archiver/app/routes/ingest.py` | Yes |
| `src/zammad_pdf_archiver/app/middleware/` | Yes |
| `src/zammad_pdf_archiver/app/jobs/process_ticket.py` | Yes |
| `src/zammad_pdf_archiver/adapters/zammad/client.py` | Yes |
| `src/zammad_pdf_archiver/adapters/zammad/models.py` | Yes |
| `src/zammad_pdf_archiver/adapters/snapshot/build_snapshot.py` | Yes |
| `src/zammad_pdf_archiver/domain/snapshot_models.py` | Yes |
| `src/zammad_pdf_archiver/adapters/pdf/template_engine.py` | Yes |
| `src/zammad_pdf_archiver/adapters/pdf/render_pdf.py` | Yes |
| `src/zammad_pdf_archiver/templates/` (default, minimal, compact) | Yes |
| `src/zammad_pdf_archiver/adapters/signing/sign_pdf.py` | Yes |
| `src/zammad_pdf_archiver/adapters/signing/tsa_rfc3161.py` | Yes |
| `src/zammad_pdf_archiver/adapters/storage/layout.py` | Yes |
| `src/zammad_pdf_archiver/adapters/storage/fs_storage.py` | Yes |
| `src/zammad_pdf_archiver/domain/path_policy.py` | Yes |
| `src/zammad_pdf_archiver/domain/state_machine.py` | Yes |
| `src/zammad_pdf_archiver/domain/errors.py` | Yes |
| `src/zammad_pdf_archiver/domain/idempotency.py` | Yes |
| `src/zammad_pdf_archiver/domain/audit.py` | Yes |
| `src/zammad_pdf_archiver/domain/html_sanitize.py` | Yes |
| `scripts/ops/verify-pdf.sh` | Yes |
| `scripts/ops/verify-pdf.py` | Yes |
| `scripts/ops/mount-cifs.sh` | Yes |
| `scripts/ci/smoke-test.sh` | Yes |
| `scripts/dev/run-local.sh` | Yes |
| `scripts/dev/gen-dev-certs.sh` | Yes |
| `.env.example` | Yes |
| `config/config.example.yaml` | Yes |
| `examples/webhook-payload.sample.json` | Yes |
| `examples/ticket-snapshot.sample.json` | Yes |
| `infra/systemd/zammad-archiver.service` | Yes |
| `infra/systemd/zammad-archiver.env` | Yes |
| `Dockerfile` | Yes |
| `docker-compose.yml` | Yes |
| `docker-compose.demo.yml` | Yes |
| Demo screenshots (01 through 10) | Yes |

### 4.2 Cross-references between docs

All inter-doc links (`docs/*.md` to `docs/*.md`) were verified as valid relative paths:
- `01-architecture.md` references ADRs -- valid
- `02-zammad-setup.md` references `07-storage.md`, `08-operations.md` -- valid
- `03-data-model.md` references `config-reference.md`, `05-pdf-rendering.md` -- valid
- `04-path-policy.md` -- self-contained, no broken refs
- `05-pdf-rendering.md` references `config-reference.md` -- valid
- `06-signing-and-timestamp.md` -- self-contained
- `07-storage.md` references `08-operations.md`, `09-security.md` -- valid
- `08-operations.md` references `release-checklist.md` -- valid
- `09-security.md` references `release-checklist.md`, `api.md` -- valid
- `faq.md` references `api.md`, `07-storage.md`, `06-signing-and-timestamp.md` -- valid
- `PRD.md` references all numbered docs -- valid
- `api.md` references `examples/webhook-payload.sample.json` -- valid

### 4.3 src/README.md

`src/README.md` lists `routes/ingest.py`, `routes/healthz.py`, `routes/metrics.py`, and middleware. It does not mention `routes/jobs.py` or `routes/admin.py`, which were added in 0.2.0-rc.1. Minor omission; the file is a developer orientation guide, not the canonical reference.

---

## 5. Missing Docstrings Inventory

Public functions and classes in `src/` that lack docstrings (private/underscore-prefixed functions excluded):

### `domain/` layer

| File | Function/Class | What it does |
|---|---|---|
| `domain/ticket_id.py` | `coerce_ticket_id()` | Coerces a value to a positive int or returns None |
| `domain/time_utils.py` | `now_utc()` | Returns current UTC datetime |
| `domain/time_utils.py` | `format_timestamp_utc()` | Formats datetime as ISO 8601 UTC string |
| `domain/state_machine.py` | `TicketTagger` (Protocol) | Protocol for tag add/remove operations |
| `domain/state_machine.py` | `should_process()` | Evaluates whether a ticket should be processed based on tags |
| `domain/state_machine.py` | `apply_processing()` | Applies processing-start tag transitions |
| `domain/state_machine.py` | `apply_done()` | Applies success tag transitions |
| `domain/state_machine.py` | `apply_error()` | Applies failure tag transitions |
| `domain/idempotency.py` | `InMemoryTTLSet` | In-memory TTL-based set for delivery ID dedup |
| `domain/audit.py` | `compute_sha256()` | Computes SHA-256 hex digest of bytes |
| `domain/error_messages.py` | `ErrorMessages` | Constants class for user-facing error message templates |
| `domain/error_messages.py` | `format_http_error()` | Formats HTTP error status into message string |
| `domain/error_messages.py` | `format_fs_error()` | Formats filesystem error into message string |
| `domain/snapshot_models.py` | `_SnapshotModel` through `Snapshot` (5 classes) | Pydantic models -- field names are self-documenting |
| `domain/path_policy.py` | `ensure_within_root()` | Ensures resolved path is under storage root |

### `adapters/` layer

| File | Function/Class | What it does |
|---|---|---|
| `adapters/storage/fs_storage.py` | `ensure_dir()` | Creates directory tree (parents=True) |
| `adapters/storage/fs_storage.py` | `write_bytes()` | Direct (non-atomic) file write with optional fsync |
| `adapters/storage/fs_storage.py` | `write_atomic_bytes()` | Atomic temp-file + replace write with optional fsync |
| `adapters/storage/fs_storage.py` | `move_file_within_root()` | Moves file within storage root with safety checks |
| `adapters/storage/layout.py` | `build_target_dir()` | Builds and validates target directory under storage root |
| `adapters/storage/layout.py` | `build_filename_from_pattern()` | Renders filename from pattern with placeholders |
| `adapters/storage/layout.py` | `build_filename()` | Legacy filename builder |
| `adapters/pdf/template_engine.py` | `render_html()` | Renders snapshot to HTML string via Jinja2 |
| `adapters/zammad/client.py` | `AsyncZammadClient` | Has no class-level docstring (methods are documented via type hints) |
| `adapters/snapshot/build_snapshot.py` | `ZammadSnapshotClient` (Protocol) | Protocol with no docstring |
| `adapters/snapshot/build_snapshot.py` | `build_snapshot()` | Builds snapshot from Zammad client data |
| `adapters/snapshot/build_snapshot.py` | `enrich_attachment_content()` | Enriches snapshot with fetched attachment binaries |

### `app/` layer

| File | Function/Class | What it does |
|---|---|---|
| `app/routes/healthz.py` | `healthz()` | Health check endpoint handler |
| `app/routes/jobs.py` | `get_queue_status()` | Queue stats endpoint handler |
| `app/routes/jobs.py` | `get_job_history()` | History endpoint handler |
| `app/routes/jobs.py` | `drain_queue_dlq()` | DLQ drain endpoint handler |
| `app/routes/jobs.py` | `get_job_status()` | Per-ticket status endpoint handler |
| `app/routes/metrics.py` | `metrics()` | Metrics endpoint handler |
| `app/routes/admin.py` | All 7 route handlers | Admin endpoint handlers |
| `app/routes/ingest.py` | `IngestPayload` | Has docstring |
| `app/server.py` | `create_app()` | Factory function for FastAPI app |
| `app/server.py` | `lifespan()` | App lifespan context manager |

### `config/` layer

| File | Function/Class | What it does |
|---|---|---|
| `config/settings.py` | All 15+ `*Settings` classes | Pydantic models -- field-level docs via comments, no class docstrings |
| `config/validate.py` | `validate_settings()` | Top-level config validation orchestrator |
| `config/validate.py` | `issues_from_pydantic_error()` | Converts Pydantic errors to ConfigValidationIssue list |
| `config/load.py` | `load_settings()` | Main entry point for loading and validating settings |

**Summary:** Most public domain/adapter functions have docstrings. The primary gaps are in route handlers (which are self-describing via their FastAPI decorators), Pydantic model classes (which are self-documenting via field declarations), and several utility functions in `domain/time_utils.py` and `domain/error_messages.py`.

---

## 6. Stale/Incorrect References

### 6.1 config-reference.md section 4: TSA_USER/TSA_PASS mislabeled

As detailed in section 2.2 above, these are part of the settings schema, not non-schema keys. The section text is incorrect.

### 6.2 src/README.md missing routes/jobs.py and routes/admin.py

`src/README.md` lists only `routes/ingest.py`, `routes/healthz.py`, and `routes/metrics.py`. The `routes/jobs.py` and `routes/admin.py` modules added in 0.2.0-rc.1 are not mentioned.

### 6.3 settings.py comment: "default|minimal"

`PdfSettings.template_variant` inline comment says `# default|minimal` but `compact` is a valid variant.

### 6.4 No stale file references found

All file paths mentioned in documentation point to files that exist in the repository.

---

## 7. Recommendations

### High priority (correctness)

1. **Add `pdf.templates_root` to config-reference.md `pdf` table.** Currently the only way to discover this setting is via `05-pdf-rendering.md` or reading the source. It should have a row in the canonical config table with default `null`, flat env alias `TEMPLATES_ROOT`, and a description noting it overrides the built-in template directory.

2. **Move `TSA_USER`/`TSA_PASS` into the `signing.timestamp.rfc3161` table in config-reference.md** and remove them from the "Non-schema Runtime Environment Keys" section. Add rows for `signing.timestamp.rfc3161.user` and `signing.timestamp.rfc3161.password`.

3. **Document `POST /admin/api/dlq/replay` and `GET /admin/api/config/check` in api.md.** Both are shipped endpoints behind admin auth and should be in the API contract doc.

### Medium priority (completeness)

4. **Document the `dry_run` query parameter** on `POST /ingest` and `POST /ingest/batch` in `docs/api.md`. This is a useful testing feature that operators should know about.

5. **Document the `deep` query parameter** on `GET /healthz` in `docs/api.md`. When `deep=true`, the endpoint checks Redis connectivity and storage writability, which is operationally significant.

6. **Update `src/README.md`** to mention `routes/jobs.py` and `routes/admin.py`.

### Low priority (polish)

7. **Fix `settings.py` inline comment** on `PdfSettings.template_variant` from `# default|minimal` to `# default|minimal|compact`.

8. **Add class docstrings to Settings section classes** in `settings.py`. While the Pydantic fields are self-documenting, a one-line class docstring improves IDE discoverability and automated documentation generation.

9. **Add docstrings to route handler functions.** FastAPI can use these as OpenAPI operation descriptions. Currently, most route handlers rely solely on their decorator for documentation.

10. **Consider adding a `TEMPLATES_ROOT` entry to `.env.example`** (commented out) for discoverability alongside other PDF settings.
