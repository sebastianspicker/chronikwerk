# Security Review: zammad-ticket-archiver

**Date:** 2026-03-21
**Scope:** All security-relevant code paths in the repository
**Reviewed commit:** a3fc68e (main)

---

## 1. Input Validation Review

### 1.1 HMAC Verification (`src/zammad_pdf_archiver/app/middleware/hmac_verify.py`)

**Strengths:**
- Uses `hmac.compare_digest()` (line 181) for constant-time signature comparison, preventing timing attacks.
- Supports both SHA-1 and SHA-256 algorithms with explicit allowlist (`_ALLOWED_ALGORITHMS`, line 18).
- Validates digest length matches expected size (line 76), rejecting truncated or padded signatures.
- Fails closed: when no secret is configured, returns 503 unless explicitly opted in via two separate flags (`allow_unsigned` AND `allow_unsigned_when_no_secret`, line 155).
- Drains request body before returning error responses (lines 149, 163, 169), preventing connection state leaks.
- Handles client disconnect during body read as auth failure (line 176-177, referencing Bug #28).
- Secret is resolved once at middleware init (line 125), not per-request, reducing risk of TOCTOU issues.

**Observations:**
- SHA-1 is still accepted (line 19). While not broken for HMAC use (HMAC-SHA1 is not vulnerable to collision attacks in the same way as bare SHA-1), it is a weaker primitive. This is acceptable for backward compatibility.
- The `_SIGNATURE_HEADER` is `X-Hub-Signature` (line 16), which is the GitHub/Zammad convention supporting `sha1=<hex>` and `sha256=<hex>` formats.

### 1.2 Body Size Limit (`src/zammad_pdf_archiver/app/middleware/body_size_limit.py`)

**Strengths:**
- Dual enforcement: checks `Content-Length` header up front (line 73) AND enforces streaming size limit via `_limited_receive_factory` (line 40-55). This prevents both declared-large and undeclared-large payloads.
- Default limit is 1 MiB (`settings.py` line 204), which is reasonable for webhook payloads.
- Only applies to `INGEST_PROTECTED_PATHS` (line 24), avoiding interference with other endpoints.

**Observations:**
- A `Content-Length` header with a non-integer value falls through to streaming enforcement (line 36), which is correct behavior.

### 1.3 Rate Limiting (`src/zammad_pdf_archiver/app/middleware/rate_limit.py`)

**Strengths:**
- Token-bucket algorithm with configurable RPS and burst (lines 26-77).
- In-memory store has bounded size (`max_entries=10_000`, line 32) with eviction (lines 45-61) to prevent memory exhaustion.
- Client key extraction from configurable header (e.g., `X-Forwarded-For`) takes only the first value (line 96), mitigating header-injection-based rate limit bypass.
- Falls back to connection peer address when no header is configured (lines 80-86).

**Observations:**
- The `client_key_header` trust model is documented ("Trust proxy to set it; use with care" in `settings.py` line 198). If deployed without a trusted reverse proxy, an attacker could spoof the header to bypass rate limits. This is a known and documented trade-off.
- The key `"unknown"` is used as fallback (lines 86, 100), meaning all unidentifiable clients share a single bucket. Under adversarial conditions this could cause legitimate traffic to be rate-limited. The impact is low since this only affects deployments with misconfigured proxies.

### 1.4 Webhook Payload Validation (`src/zammad_pdf_archiver/app/routes/ingest.py`)

**Strengths:**
- `IngestPayload` uses Pydantic model validation (lines 24-40) to require a resolvable positive integer ticket ID.
- `extract_ticket_id()` in `domain/ticket_id.py` rejects booleans, non-positive integers, and non-numeric strings (lines 6-24). This prevents type confusion attacks.
- Batch endpoint (`/ingest/batch`, line 140) validates each payload individually via the same `IngestPayload` model.

**Findings:**
- The `/retry/{ticket_id}` endpoint (line 173) is NOT included in `INGEST_PROTECTED_PATHS` and therefore receives NO HMAC verification, NO body size limiting, and NO dedicated rate limiting. Any unauthenticated caller can trigger ticket reprocessing for arbitrary ticket IDs. See Finding F-01.

### 1.5 HTML Sanitization (`src/zammad_pdf_archiver/domain/html_sanitize.py`)

**Strengths:**
- Strict allowlist approach: only explicitly listed tags (`_ALLOWED_TAGS`, lines 9-41) and attributes (`_ALLOWED_ATTRS`, lines 64-68) pass through.
- Dangerous tags (`script`, `style`, `iframe`, `object`, `embed`, `form`, etc.) are dropped WITH their content (lines 43-59).
- Event handler attributes are blocked by rejecting any attribute starting with `on` (line 152).
- `style` attribute is explicitly blocked (line 152).
- URL scheme validation limits `href` to `http`, `https`, `mailto`, and same-origin relative URLs (line 70). This blocks `javascript:`, `data:`, `file:`, and `vbscript:` schemes.
- Scheme-relative URLs (`//example.com`) are explicitly rejected (line 82).
- Null bytes in URLs are rejected (line 75).
- Nesting depth is limited to 50 (line 130) to prevent stack/resource exhaustion.
- Fails closed: any exception returns empty string (lines 220-222).
- Output is escaped with `html.escape()` for data content (line 187) and attributes (line 114).

**Observations:**
- The sanitizer is purpose-built for PDF rendering (not browser display), which reduces the attack surface. The allowlist is appropriately minimal for this use case.

---

## 2. Path Traversal Protection Review

### 2.1 Path Policy (`src/zammad_pdf_archiver/domain/path_policy.py`)

**Strengths:**
- `sanitize_segment()` reduces input to ASCII `[A-Za-z0-9._-]` only (line 7), eliminating all special filesystem characters.
- `validate_segments()` enforces max depth (default 10), max segment length (default 64), and rejects:
  - Dot segments `.` and `..` (line 79)
  - Null bytes (line 81)
  - Path separators `/` and `\` (line 83)
  - Empty segments (line 78)
- `ensure_within_root()` resolves both root and target with `Path.resolve(strict=False)` then checks `is_relative_to()` (lines 90-95). This catches symlink-based traversals at the logical path level.

### 2.2 Filesystem Storage (`src/zammad_pdf_archiver/adapters/storage/fs_storage.py`)

**Strengths:**
- `_validate_and_prepare()` (line 33) calls `ensure_within_root()` before any directory creation or file write.
- `_reject_symlinks_under_root()` (line 64) walks every path component under root and rejects symlinks, mitigating symlink race attacks (with acknowledged TOCTOU limitation in comment on line 67).
- `write_bytes()` uses `O_NOFOLLOW` flag (line 50) where available, preventing writes through symlinks at the OS level.
- Files are created with mode `0o640` (lines 56, 118), not world-readable.
- `write_atomic_bytes()` uses `tempfile.mkstemp()` in the same directory (line 98) then `os.replace()` (line 125) for atomic writes, preventing partial-file exposure.
- `move_file_within_root()` validates both source and destination paths (lines 163-165).
- `fsync` is performed on both file and parent directory for durability.

### 2.3 Layout (`src/zammad_pdf_archiver/adapters/storage/layout.py`)

**Strengths:**
- `build_target_dir()` performs validation on raw inputs, sanitization, re-validation of sanitized output, AND `ensure_within_root()` as final check (line 73). This is defense-in-depth.
- `allow_prefixes` policy (lines 53-67) provides allowlist-based path restriction. Empty list means no path is allowed (Bug #30, line 54).
- `build_filename_from_pattern()` rejects path separators, null bytes, dot segments, and enforces max length of 255 (lines 120-127).

**Observations:**
- The TOCTOU race in symlink checking (`fs_storage.py` line 67) is inherent to filesystem operations and is documented. The `O_NOFOLLOW` flag provides a second layer of defense.

---

## 3. Secret Management Review

### 3.1 Settings (`src/zammad_pdf_archiver/config/settings.py`)

**Strengths:**
- All secrets use `pydantic.SecretStr` type: `api_token` (line 27), `webhook_hmac_secret` (line 28), `webhook_shared_secret` (line 21), `pfx_password` (line 128), `key_password` (line 127), TSA `password` (line 137), `metrics_bearer_token` (line 177), `bearer_token` (line 237).
- `SecretStr` prevents accidental exposure in `repr()`, `str()`, logging, and serialization.
- `extra="forbid"` (line 15) on all model sections rejects unexpected fields, preventing config injection.

### 3.2 Log Redaction (`src/zammad_pdf_archiver/config/redact.py`)

**Strengths:**
- `redact_settings_dict()` deep-redacts by both key name (lines 91-97) and value type (`SecretStr`, line 101).
- Sensitive key detection covers explicit names, suffix patterns (`_pass`), and fragment matching (`password`, `token`, `secret`, `authorization`, `api_key`, `apikey`) (lines 10-26).
- `scrub_secrets_in_text()` applies regex-based redaction for free-form text covering: `Authorization` headers, Zammad `Token token=` format, common key=value patterns, JSON-style secrets, env-var style lines, and query parameters (lines 55-88).

**Observations:**
- Regex-based text redaction is inherently best-effort. Novel secret formats could slip through. This is an appropriate trade-off for log usability.

### 3.3 Admin Bearer Token (`src/zammad_pdf_archiver/app/routes/admin.py`)

**Strengths:**
- Uses `hmac.compare_digest()` for constant-time comparison (line 47).
- Requires admin to be explicitly enabled AND token to be configured (lines 34-40). Missing token returns 503, not bypass.
- Every admin endpoint calls `_verify_admin_auth()` (lines 59, 75, 90, 111, 128, 143).

### 3.4 Metrics Bearer Token (`src/zammad_pdf_archiver/app/routes/metrics.py`)

**Strengths:**
- Uses `hmac.compare_digest()` for constant-time comparison (line 29).
- Empty expected token causes authentication failure (line 28: `not expected`).

**Observations:**
- When `metrics_bearer_token` is `None` (not configured), the metrics endpoint is unprotected (line 22). This is intentional (opt-in auth), but operators should be aware that metrics can leak operational information.

---

## 4. Transport Security Review

### 4.1 Configuration Validation (`src/zammad_pdf_archiver/config/validate.py`)

**Strengths:**
- Blocks plaintext HTTP upstream URLs by default (lines 112-124). Requires explicit `allow_insecure_http=true` to override.
- Blocks disabled TLS verification by default (lines 126-135). Requires explicit `allow_insecure_tls=true`.
- Blocks loopback/link-local upstream hosts by default (lines 40-50, 137-142), mitigating SSRF to internal services.
- Validates TSA URLs with the same transport security policy (lines 180-206).
- Validates admin token presence when admin is enabled (lines 209-222).
- IP address checking covers loopback, link-local, AND unspecified addresses (line 50).

### 4.2 Zammad Client (`src/zammad_pdf_archiver/adapters/zammad/client.py`)

**Strengths:**
- `trust_env=False` by default (line 43/73), preventing proxy environment variable injection.
- `follow_redirects=False` (line 74), preventing open redirect abuse / credential forwarding.
- Connection limits are set (`max_connections=10`, `max_keepalive_connections=5`, line 67-70), preventing resource exhaustion.
- Timeout is bounded with fail-fast connect timeout (via `timeouts_for()` in `http_util.py`, lines 9-13).
- Retry logic has bounded max retries (default 3, line 27) with exponential backoff, preventing infinite retry loops.
- URL path validation requires scheme and host (lines 49-50).

**Observations:**
- Error messages from `_raise_for_status()` include the request URL (lines 275-285). The URL should not contain credentials (API token is in header, not URL), so this is acceptable.

---

## 5. CI/CD Security Review

### 5.1 CI Workflow (`.github/workflows/ci.yml`)

**Strengths:**
- Top-level `permissions: contents: read` (line 13) follows least-privilege principle.
- All actions are pinned to full SHA hashes (e.g., `actions/checkout@34e114...`, line 21).
- `persist-credentials: false` on checkout (line 23) prevents credential leakage to subsequent steps.
- `timeout-minutes: 15` (line 18) prevents runaway jobs.
- Concurrency with `cancel-in-progress: true` (line 10) prevents resource waste.

### 5.2 Docker Workflow (`.github/workflows/docker.yml`)

**Strengths:**
- Top-level `permissions: contents: read` (line 14); job-level adds only `packages: write` for GHCR push (line 25-26).
- All actions pinned to SHA hashes.
- `persist-credentials: false` on checkout (line 31).
- Push is conditional on GHCR token availability (lines 48-54) and skips RC tags.
- Uses BuildKit cache (`cache-from: type=gha`, lines 72-73).

### 5.3 Security Workflow (`.github/workflows/security.yml`)

**Strengths:**
- Weekly scheduled vulnerability scanning (`cron: "0 9 * * 1"`, line 15).
- Fail-closed policy: unknown severity vulnerabilities cause failure (lines 216-220).
- CRITICAL and HIGH findings block the pipeline (lines 204-214).
- All actions pinned to SHA hashes.

### 5.4 RC Release Workflow (`.github/workflows/rc-release.yml`)

**Strengths:**
- Actions pinned to SHA hashes.
- `persist-credentials: false` on checkout.
- SHA256 checksums generated for release artifacts (line 41).
- Uses `softprops/action-gh-release` pinned to SHA (line 61).

**Observations:**
- `permissions: contents: write` (line 15) is necessary for release creation but is broader than some other workflows. This is acceptable given the workflow only triggers on RC tags.

### 5.5 Dockerfile

**Strengths:**
- Multi-stage build separates builder from runtime (lines 1, 18), keeping build tools out of the final image.
- Non-root user `app` with high UID/GID 10001 (lines 37-38).
- `USER app:app` (line 44) is set before `CMD`.
- No `--privileged`, no capabilities added.
- Home directory set to `/nonexistent` (line 38), preventing home directory abuse.
- Shell set to `/usr/sbin/nologin` (line 38).
- `apt` lists cleaned after install (line 35).

**Observations:**
- No `HEALTHCHECK` instruction is present. While not strictly a security issue, adding one could improve orchestration resilience.
- No `LABEL` for OCI security metadata (maintainer, source URL). This is cosmetic.

---

## 6. Error Information Leakage Review

### 6.1 Server (`src/zammad_pdf_archiver/app/server.py`)

**Strengths:**
- Global exception handler (lines 48-59) catches all unhandled exceptions and returns a generic `"An internal server error occurred."` message with code `"internal_error"`. The original exception details are NOT exposed to the client.
- Request ID is included in error responses for correlation without revealing internals.

### 6.2 Responses (`src/zammad_pdf_archiver/app/responses.py`)

**Strengths:**
- `api_error()` returns structured JSON with `detail`, `code`, optional `hint`, and `request_id` (lines 19-36). No stack traces or internal paths are included.
- All middleware error responses use this function with generic, code-style error messages (e.g., `"forbidden"`, `"rate_limited"`, `"request_too_large"`).

### 6.3 Healthz (`src/zammad_pdf_archiver/app/routes/healthz.py`)

**Observations:**
- By default, `/healthz` exposes service name and version (lines 54-55). The `healthz_omit_version` setting (checked at line 53) allows suppressing this. Operators in sensitive environments should enable this option.
- Deep health check (`?deep=true`) exposes storage path (line 41) and exception message excerpts (lines 34, 43, truncated to 200 chars). The storage path reveals internal filesystem layout. The exception excerpts could leak internal details. These are only exposed when `deep=true` is explicitly requested.

---

## 7. Findings

### F-01: `/retry/{ticket_id}` Endpoint Lacks Authentication [HIGH]

**Location:** `src/zammad_pdf_archiver/app/routes/ingest.py`, line 173-191
**Issue:** The `POST /retry/{ticket_id}` endpoint is not included in `INGEST_PROTECTED_PATHS` (defined in `constants.py` as `{"/ingest", "/ingest/batch"}`). As a result, it receives no HMAC signature verification, no body size limiting, and no dedicated rate limiting. Any network-reachable client can trigger ticket reprocessing for any ticket ID.
**Impact:** An attacker could trigger mass reprocessing of tickets, causing denial-of-service via Zammad API load, or force PDF regeneration with potentially different content if ticket data has changed.
**Recommendation:** Either add `/retry/{ticket_id}` to a protected path set with appropriate authentication (HMAC or bearer token), or require admin auth for this endpoint (similar to `/admin/api/retry/{ticket_id}` which already exists with proper auth).

### F-02: Deep Health Check Leaks Internal Filesystem Path [LOW]

**Location:** `src/zammad_pdf_archiver/app/routes/healthz.py`, lines 41, 34, 43
**Issue:** The `?deep=true` health check response includes `"path": str(root)` (the storage root directory) and truncated exception messages that may reveal internal configuration.
**Impact:** Information disclosure of internal filesystem layout. Useful for reconnaissance but not directly exploitable.
**Recommendation:** Consider omitting the filesystem path from the response, or gate deep health checks behind authentication.

### F-03: Metrics Endpoint Unprotected by Default [LOW]

**Location:** `src/zammad_pdf_archiver/app/routes/metrics.py`, lines 20-22
**Issue:** When `metrics_bearer_token` is not configured, the `/metrics` endpoint is accessible without authentication. Metrics can reveal operational information (request counts, error rates, queue depths).
**Impact:** Information disclosure useful for reconnaissance. Low severity since metrics exposure is a common pattern and the endpoint is opt-in.
**Recommendation:** Document that operators should configure `metrics_bearer_token` in production environments. Consider logging a warning at startup when metrics are enabled without a token.

### F-04: SHA-1 HMAC Still Accepted [INFORMATIONAL]

**Location:** `src/zammad_pdf_archiver/app/middleware/hmac_verify.py`, line 19
**Issue:** The HMAC verification accepts `sha1=<hex>` signatures. While HMAC-SHA1 is not practically broken (collision attacks on bare SHA-1 do not apply to HMAC constructions), SHA-256 is preferred.
**Impact:** Negligible. HMAC-SHA1 remains secure for authentication purposes.
**Recommendation:** Consider deprecating SHA-1 support in a future version, or logging a warning when SHA-1 is used, to encourage migration to SHA-256.

### F-05: TOCTOU Race in Symlink Rejection [INFORMATIONAL]

**Location:** `src/zammad_pdf_archiver/adapters/storage/fs_storage.py`, line 67
**Issue:** The symlink check and subsequent file write are not atomic, creating a theoretical time-of-check-to-time-of-use race window. This is documented in the code.
**Impact:** Requires local filesystem access by an attacker (i.e., already compromised), making this theoretical in typical deployment scenarios. The `O_NOFOLLOW` flag provides an additional OS-level mitigation.
**Recommendation:** No action required. The defense-in-depth approach (validate + O_NOFOLLOW) is appropriate.

### F-06: Rate Limit Bypass via Spoofed Client Key Header [INFORMATIONAL]

**Location:** `src/zammad_pdf_archiver/app/middleware/rate_limit.py`, lines 89-106
**Issue:** When `client_key_header` is configured (e.g., `X-Forwarded-For`), an attacker without a trusted proxy in the path can rotate the header value to get a fresh rate limit bucket per request.
**Impact:** Rate limit bypass, but only in misconfigured deployments (no trusted reverse proxy). Documented in settings.
**Recommendation:** Already documented. No additional action needed beyond ensuring deployment documentation emphasizes trusted proxy requirement.

---

## 8. Recommendations

### Priority 1 (Address promptly)
1. **Protect `/retry/{ticket_id}`**: Add authentication to this endpoint. The simplest approach is to add it to `INGEST_PROTECTED_PATHS` for HMAC protection, or alternatively require the same admin bearer token used by `/admin/api/retry/{ticket_id}`.

### Priority 2 (Address in next release)
2. **Sanitize deep health check output**: Remove the storage root path from the `?deep=true` response. Consider gating deep checks behind a bearer token or only exposing boolean pass/fail results.
3. **Warn on unprotected metrics**: Log a startup warning when `metrics_enabled=true` but `metrics_bearer_token` is not configured.

### Priority 3 (Consider for future versions)
4. **Deprecate HMAC-SHA1**: Add a deprecation notice and log warning when SHA-1 signatures are used, guiding callers toward SHA-256.
5. **Add Dockerfile HEALTHCHECK**: While not a security vulnerability, a `HEALTHCHECK` instruction improves container orchestration resilience.
6. **Add OCI labels to Dockerfile**: Standard labels (`org.opencontainers.image.source`, etc.) improve supply chain traceability.

---

## Summary

The codebase demonstrates strong security engineering practices overall:
- Defense-in-depth is applied consistently (input validation, path sanitization, filesystem checks, `O_NOFOLLOW`).
- Secrets are properly typed (`SecretStr`), redacted in logs, and compared in constant time.
- CI workflows follow supply-chain security best practices (SHA-pinned actions, minimal permissions, `persist-credentials: false`).
- Error responses are generic and do not leak stack traces or internal details.
- Transport security is enforced by default with explicit opt-out required for insecure configurations.

The one HIGH-severity finding (F-01: unauthenticated `/retry/{ticket_id}`) should be addressed promptly. All other findings are LOW or INFORMATIONAL.
