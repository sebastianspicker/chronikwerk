# API

This document defines the HTTP contract for `zammad-pdf-archiver`.

## 1. Endpoints

### `POST /ingest`

Webhook ingestion endpoint.

Path variants such as `/ingest/` and `/ingest%2F` are treated as protected
ingest paths by HMAC, body-size, and rate-limit middleware before routing or
redirect handling.

#### Ingest query parameters

None. Unknown query parameters have no special route-level behavior.

#### Ingest request headers

- `Content-Type: application/json` (recommended)
- `X-Request-Id: <id>` (optional)
- `X-Hub-Signature: sha1=<hex>` or `sha256=<hex>` (required when secret is configured)
- `X-Zammad-Delivery: <id>` (required by default; disable only for controlled tests with `hardening.webhook.require_delivery_id=false`)

#### Ingest request body

JSON object. Ticket ID is extracted from either:
- `ticket.id`
- `ticket_id`

If ticket ID is missing or invalid, the request is rejected with `422` (schema validation); valid payloads get `202` and background processing.

Example payload:
- [`../examples/webhook-payload.sample.json`](../examples/webhook-payload.sample.json)

#### Ingest success response

- status: `202`
- body: `{"status":"accepted","ticket_id":123}`
- header: `X-Request-Id` is always returned

#### Ingest error responses

- `400` `{"detail":"missing_delivery_id"}`
- `403` `{"detail":"forbidden"}`
- `422` invalid body (e.g. missing or invalid ticket id)
- `413` `{"detail":"request_too_large","code":"request_too_large"}`; requests with an over-limit `Content-Length` are rejected before the application reads the body, and streaming requests stop at the first chunk that exceeds the limit.
- `429` `{"detail":"rate_limited"}`
- `503` `{"detail":"webhook_auth_not_configured"}`

### `POST /ingest/batch`

Batch webhook ingestion endpoint.

Path variants such as `/ingest/batch/` are treated as protected ingest paths by
HMAC, body-size, and rate-limit middleware before routing or redirect handling.

#### Batch query parameters

None. Unknown query parameters have no special route-level behavior.

#### Batch request headers

- `Content-Type: application/json` (recommended)
- `X-Request-Id: <id>` (optional)
- `X-Hub-Signature: sha1=<hex>` or `sha256=<hex>` (required when secret is configured)
- `X-Zammad-Delivery: <id>` (required by default; disable only for controlled tests with `hardening.webhook.require_delivery_id=false`)

#### Batch request body

JSON array of ingest payload objects (maximum **100** items per request). Each item must contain either:
- `ticket.id`
- `ticket_id`

When `X-Zammad-Delivery` is present, the service derives per-item delivery IDs as `<delivery-id>:<index>` (zero-based) before applying idempotency checks.

#### Batch success response

- status: `202`
- body: `{"status":"accepted","count":<int>}`
- header: `X-Request-Id` is always returned

#### Batch error responses

- `400` `{"detail":"missing_delivery_id"}`
- `403` `{"detail":"forbidden"}`
- `422` invalid body (e.g. missing or invalid ticket id in an item), or batch exceeds 100 items (`{"detail":"batch_too_large"}`)
- `413` `{"detail":"request_too_large","code":"request_too_large"}`; requests with an over-limit `Content-Length` are rejected before the application reads the body, and streaming requests stop at the first chunk that exceeds the limit.
- `429` `{"detail":"rate_limited"}`
- `503` `{"detail":"webhook_auth_not_configured"}` or `{"detail":"shutting_down"}`
- `503` `{"status":"partial_failure","code":"batch_dispatch_failed","accepted":<int>,"failed_index":<int>,"failed_ticket_id":<int>}` when dispatch fails after earlier items were accepted. The accepted count is the number of jobs already dispatched; clients should not assume the whole batch was rejected.

### `POST /retry/{ticket_id}`

Schedules one forced reprocessing run for a specific ticket ID.
Requires `Authorization: Bearer <ADMIN_BEARER_TOKEN>`.

Behavior notes:
- bypasses trigger-tag gating
- bypasses `pdf:signed` skip behavior
- still skips delivery-ID dedupe by using no delivery ID

#### Retry request headers

- `Authorization: Bearer <ADMIN_BEARER_TOKEN>` (required)

#### Retry path parameters

- `ticket_id` (int, required)

#### Retry success response

- status: `202`
- body: `{"status":"accepted","ticket_id":<int>}`

#### Retry error responses

- `401` missing/invalid bearer token
- `503` `{"detail":"admin_token_not_configured"}` or `{"detail":"settings_not_configured"}`

### `GET /jobs/{ticket_id}`

Returns best-known job status for one ticket.
Requires `Authorization: Bearer <ADMIN_BEARER_TOKEN>`.

#### Job status request headers

- `Authorization: Bearer <ADMIN_BEARER_TOKEN>` (required)

#### Job status response

- status: `200`
- body:

```json
{
  "ticket_id": 123,
  "in_flight": false,
  "process_local_in_flight": false,
  "distributed_in_flight": null,
  "shutting_down": false
}
```

Notes:
- `in_flight` is the best-known union of process-local state and Redis ticket-lock state.
- `process_local_in_flight` is non-persistent and resets on process restart.
- `distributed_in_flight` is `true` or `false` when Redis ticket-lock state is configured and readable; otherwise it is `null`.

#### Job status error responses

- `401` missing/invalid bearer token
- `503` `{"detail":"admin_token_not_configured"}`, `{"detail":"settings_not_configured"}`, or `{"detail":"ticket_lock_unavailable"}`

### `GET /jobs/queue/stats`

Returns queue status for the configured execution backend.
Requires `Authorization: Bearer <ADMIN_BEARER_TOKEN>`.

#### Queue stats request headers

- `Authorization: Bearer <ADMIN_BEARER_TOKEN>` (required)

#### Response (in-process backend)

```json
{
  "execution_backend": "inprocess",
  "queue_enabled": false
}
```

#### Response (redis queue backend)

```json
{
  "execution_backend": "redis_queue",
  "queue_enabled": true,
  "stream": "zammad:jobs",
  "group": "zammad:jobs:workers",
  "consumer": "host-12345",
  "queue_depth": 0,
  "pending": 0,
  "dlq_stream": "zammad:jobs:dlq",
  "dlq_depth": 0,
  "retry_max_attempts": 3,
  "history_stream": "zammad:jobs:history",
  "history_retention_maxlen": 5000
}
```

#### Queue stats error responses

- `401` missing/invalid bearer token
- `503` `{"detail":"admin_token_not_configured"}` or `{"detail":"settings_not_configured"}`
- `503` `{"detail":"queue_unavailable"}` when the queue backend is unavailable

### `GET /jobs/history`

Returns processing history events from Redis history stream when history is enabled.
Requires `Authorization: Bearer <ADMIN_BEARER_TOKEN>`.

Query parameters:
- `limit` (optional, default `100`, max `5000`)
- `ticket_id` (optional int filter)

Error responses:
- `401` missing/invalid bearer token
- `503` admin token missing or history backend unavailable

Response:

```json
{
  "status": "ok",
  "available": true,
  "count": 2,
  "truncated": false,
  "items": [
    {
      "id": "1710000000000-0",
      "status": "processed",
      "ticket_id": 123,
      "classification": null,
      "message": "",
      "delivery_id": "delivery-1",
      "request_id": "req-1",
      "created_at": 1710000000.0
    }
  ]
}
```

When history retention/backend is disabled, the response is:

```json
{
  "status": "disabled",
  "available": false,
  "count": 0,
  "truncated": false,
  "items": []
}
```

Known history status values include `processed`,
`processed_done_update_failed`, `failed_transient`, `failed_permanent`, and
`skipped_*`. `processed_done_update_failed` means the archive was written, but
the final Zammad done-tag transition failed and needs operator attention.
`truncated: true` means the response reached the requested limit and more
history may exist.

### `POST /jobs/queue/dlq/drain`

Delete dead-letter queue entries from the Redis stream without replaying them.
Requires `Authorization: Bearer <ADMIN_BEARER_TOKEN>`.

Query parameters:
- `limit` (optional, default `100`, max `1000`)

Error responses:
- `401` missing/invalid bearer token
- `503` admin token missing or DLQ backend unavailable

Response:

```json
{
  "status": "ok",
  "drained": 12,
  "selected": 12,
  "deleted": 12,
  "not_deleted": 0
}
```

`status` is `partial` when Redis did not confirm deletion for every selected
DLQ entry. `drained` is kept as an alias for the confirmed `deleted` count.

### `GET /healthz`

Always available.

#### Health query parameters

  - `deep` (bool, optional, default `false`) -- when `true`, performs deep health checks (Redis ping, storage write/free-space test) and includes a `checks` object in the response. The top-level `status` field may return `"degraded"` if any deep check fails.

#### Example response (shallow)

```json
{
  "status": "ok",
  "service": "zammad-pdf-archiver",
  "version": "...",
  "time": "2026-02-07T12:00:00+00:00"
}
```

#### Example response (deep)

```json
{
  "status": "ok",
  "service": "zammad-pdf-archiver",
  "version": "...",
  "time": "2026-02-07T12:00:00+00:00",
  "checks": {
    "redis": { "available": true },
    "storage": { "writable": true, "free_bytes": 1073741824 }
  }
}
```

When a deep check fails, the response may look like:

```json
{
  "status": "degraded",
  "service": "zammad-pdf-archiver",
  "version": "...",
  "time": "2026-02-07T12:00:00+00:00",
  "checks": {
    "redis": { "available": false, "reason": "not_configured" },
    "storage": { "writable": true, "free_bytes": 1073741824 }
  }
}
```

Notes:
- `version` comes from installed package metadata; fallback may be `0.0.0` in some non-packaged contexts.
- When `HEALTHZ_OMIT_VERSION=true`, the response contains only `status` and `time` (no `service` or `version`).
- The `checks` object is only present when `deep=true`.
- `checks.storage.free_bytes` is reported when the storage write probe and filesystem free-space probe both succeed.

### `GET /metrics`

Only mounted when `observability.metrics_enabled=true`. `METRICS_BEARER_TOKEN` is required whenever metrics are enabled. Requests must include `Authorization: Bearer <token>`; missing or invalid tokens return `401`. A metrics route constructed without a configured token returns `503` instead of exposing metrics.

Response format:
- Prometheus text exposition (`text/plain`)

### `GET /admin`

Returns a lightweight admin dashboard HTML shell. Requires `admin.enabled=true` and either:
- `Authorization: Bearer <ADMIN_BEARER_TOKEN>`
- HTTP Basic auth; the username is ignored and the password must equal `ADMIN_BEARER_TOKEN`

Missing or invalid dashboard auth returns `401` with `WWW-Authenticate: Basic realm="zammad-pdf-archiver-admin"`.

### Admin API (`/admin/api/*`)

All admin API endpoints require:
- `admin.enabled=true`
- `Authorization: Bearer <ADMIN_BEARER_TOKEN>`

Endpoints:
- `GET /admin/api/queue/stats`
- `GET /admin/api/history`
- `POST /admin/api/retry/{ticket_id}`
- `POST /admin/api/dlq/drain`
- `POST /admin/api/dlq/replay`
- `GET /admin/api/config/check`

### `POST /admin/api/dlq/replay`

Replays dead-letter queue entries back to the main processing queue.

#### DLQ replay query parameters

- `limit` (int, optional, default `10`, max `1000`)

#### DLQ replay success response

- status: `200`
- body:

```json
{
  "status": "ok",
  "idempotent": false,
  "duplicate_risk": 0,
  "selected": 6,
  "replayed": 5,
  "deleted": 5,
  "skipped": 1,
  "errors": 0,
  "not_deleted": 0
}
```

`status` is `partial` when any selected DLQ entry was skipped, failed to
enqueue, or was replayed without Redis confirming deletion of the original DLQ
entry.

Replay is not idempotent. If a replayed entry cannot be deleted from the DLQ,
calling replay again can enqueue the same ticket again. `duplicate_risk` is the
count of replayed entries whose original DLQ entry was not confirmed deleted;
`not_deleted` is kept as the raw compatibility alias.

Malformed DLQ entries are not deleted during replay. They are counted as
`skipped` so operators can inspect or drain them explicitly.

#### DLQ replay error responses

- `401` missing/invalid bearer token
- `404` admin disabled
- `503` admin token not configured or DLQ backend unavailable

### `GET /admin/api/config/check`

Returns configuration validation status, including whether the current settings pass all validation checks and the state of key runtime dependencies.

#### Config check success response

- status: `200`
- body:

```json
{
  "valid": true,
  "issues": [],
  "checks": {
    "storage_root_exists": true,
    "signing_enabled": true,
    "pfx_file_exists": true
  }
}
```

When validation issues are found:

```json
{
  "valid": false,
  "issues": [
    { "path": "storage.root", "message": "directory does not exist" }
  ],
  "checks": {
    "storage_root_exists": false,
    "signing_enabled": false
  }
}
```

Notes:
- `checks.pfx_file_exists` is only present when signing is enabled and a PFX path is configured.

#### Config check error responses

- `401` missing/invalid bearer token
- `404` admin disabled
- `503` admin token not configured

## 2. Webhook Security Contract

### HMAC verification

When a secret is configured:
- header: `X-Hub-Signature`
- format: `sha1=<hex>` or `sha256=<hex>`
- algorithms: HMAC-SHA1 and HMAC-SHA256 (sender chooses; prefer SHA-256 for new setups)
- message: raw request body bytes

Set `hardening.webhook.webhook_reject_sha1=true` to reject SHA-1 signatures and
accept SHA-256 only.

Secret sources:
- preferred: `zammad.webhook_hmac_secret` (`WEBHOOK_HMAC_SECRET`)
- legacy fallback: `server.webhook_shared_secret` (`WEBHOOK_SHARED_SECRET`)

### Unsigned mode

Default is fail-closed.

To allow unsigned requests (internal testing only):
- `hardening.webhook.allow_unsigned=true`
- if no webhook secret is configured, also set
  `hardening.webhook.allow_unsigned_when_no_secret=true`

### Delivery ID requirement

Optional strict mode:
- set `hardening.webhook.require_delivery_id=true`
- then `X-Zammad-Delivery` is mandatory

## 3. Idempotency Contract

`X-Zammad-Delivery` is used for best-effort dedupe:
- duplicate delivery IDs are skipped for `workflow.delivery_id_ttl_seconds`
- dedupe state is in-memory by default and not durable across restarts
- with `workflow.idempotency_backend=redis`, dedupe is durable across restarts/multiple workers

## 4. Example Signed Request

SHA-1 (Zammad typically sends this):

```bash
sig="sha1=$(openssl dgst -sha1 -hmac "$WEBHOOK_HMAC_SECRET" -hex payload.json | awk '{print $2}')"
curl -i \
  -H "Content-Type: application/json" \
  -H "X-Hub-Signature: $sig" \
  -H "X-Zammad-Delivery: delivery-001" \
  --data-binary @payload.json \
  http://127.0.0.1:8080/ingest
```

SHA-256 is also accepted: use `sha256=<hex>` in the header and compute HMAC-SHA256 over the raw body.
