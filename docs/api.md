# API

This document defines the HTTP contract for `zammad-pdf-archiver`.

## 1. Endpoints

### `POST /ingest`

Webhook ingestion endpoint.

#### Query parameters

- `dry_run` (bool, optional, default `false`) -- when `true`, validates the payload and returns `202` with `{"status":"dry_run_accepted","ticket_id":<int>}` without dispatching any background work. Useful for integration testing.

#### Request headers

- `Content-Type: application/json` (recommended)
- `X-Request-Id: <id>` (optional)
- `X-Hub-Signature: sha1=<hex>` or `sha256=<hex>` (required when secret is configured)
- `X-Zammad-Delivery: <id>` (required only when `hardening.webhook.require_delivery_id=true`)

#### Request body

JSON object. Ticket ID is extracted from either:
- `ticket.id`
- `ticket_id`

If ticket ID is missing or invalid, the request is rejected with `422` (schema validation); valid payloads get `202` and background processing.

Example payload:
- [`../examples/webhook-payload.sample.json`](../examples/webhook-payload.sample.json)

#### Success response

- status: `202`
- body: `{"status":"accepted","ticket_id":123}`
- header: `X-Request-Id` is always returned

#### Error responses

- `400` `{"detail":"missing_delivery_id"}`
- `403` `{"detail":"forbidden"}`
- `422` invalid body (e.g. missing or invalid ticket id)
- `413` `{"detail":"request_too_large"}`
- `429` `{"detail":"rate_limited"}`
- `503` `{"detail":"webhook_auth_not_configured"}`

### `POST /ingest/batch`

Batch webhook ingestion endpoint.

#### Query parameters

- `dry_run` (bool, optional, default `false`) -- when `true`, validates the payload and returns `202` with `{"status":"dry_run_accepted","count":<int>}` without dispatching any background work. Useful for integration testing.

#### Request body

JSON array of ingest payload objects (maximum **100** items per request). Each item must contain either:
- `ticket.id`
- `ticket_id`

#### Success response

- status: `202`
- body: `{"status":"accepted","count":<int>}`
- header: `X-Request-Id` is always returned

#### Error responses

- `403` `{"detail":"forbidden"}`
- `422` invalid body (e.g. missing or invalid ticket id in an item), or batch exceeds 100 items (`{"detail":"batch_too_large"}`)
- `413` `{"detail":"request_too_large"}`
- `429` `{"detail":"rate_limited"}`
- `503` `{"detail":"webhook_auth_not_configured"}` or `{"detail":"shutting_down"}`

### `POST /retry/{ticket_id}`

Schedules one retry run for a specific ticket ID.
Requires `Authorization: Bearer <ADMIN_BEARER_TOKEN>`.

#### Request headers

- `Authorization: Bearer <ADMIN_BEARER_TOKEN>` (required)

#### Path parameters

- `ticket_id` (int, required)

#### Success response

- status: `202`
- body: `{"status":"accepted","ticket_id":<int>}`

#### Error responses

- `401` missing/invalid bearer token
- `503` `{"detail":"admin_token_not_configured"}` or `{"detail":"settings_not_configured"}`

### `GET /jobs/{ticket_id}`

Returns process-local job status for one ticket.

#### Response

- status: `200`
- body:

```json
{
  "ticket_id": 123,
  "in_flight": false,
  "shutting_down": false
}
```

Notes:
- `in_flight` is process-local and non-persistent.
- Status is reset on process restart.

### `GET /jobs/queue/stats`

Returns queue status for the configured execution backend.

#### Response (in-process backend)

```json
{
  "execution_backend": "inprocess",
  "queue_enabled": false
}
```

### `GET /jobs/history`

Returns processing history events from Redis history stream.
Requires `Authorization: Bearer <ADMIN_BEARER_TOKEN>`.

Query parameters:
- `limit` (optional, default `100`, max `5000`)
- `ticket_id` (optional int filter)

Error responses:
- `401` missing/invalid bearer token
- `503` ops token missing or history backend unavailable

Response:

```json
{
  "status": "ok",
  "count": 2,
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

### `POST /jobs/queue/dlq/drain`

Drain dead-letter queue entries from Redis stream.
Requires `Authorization: Bearer <ADMIN_BEARER_TOKEN>`.

Query parameters:
- `limit` (optional, default `100`, max `1000`)

Error responses:
- `401` missing/invalid bearer token
- `503` ops token missing or DLQ backend unavailable

Response:

```json
{
  "status": "ok",
  "drained": 12
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
  "retry_max_attempts": 3
}
```

### `GET /healthz`

Always available.

#### Query parameters

- `deep` (bool, optional, default `false`) -- when `true`, performs deep health checks (Redis ping, storage write test) and includes a `checks` object in the response. The top-level `status` field may return `"degraded"` if any deep check fails.

#### Example response (shallow)

```json
{
  "status": "ok",
  "service": "zammad-pdf-archiver",
  "version": "0.1.0",
  "time": "2026-02-07T12:00:00+00:00"
}
```

#### Example response (deep)

```json
{
  "status": "ok",
  "service": "zammad-pdf-archiver",
  "version": "0.1.0",
  "time": "2026-02-07T12:00:00+00:00",
  "checks": {
    "redis": { "available": true },
    "storage": { "writable": true, "path": "/data/archive" }
  }
}
```

When a deep check fails, the response may look like:

```json
{
  "status": "degraded",
  "service": "zammad-pdf-archiver",
  "version": "0.1.0",
  "time": "2026-02-07T12:00:00+00:00",
  "checks": {
    "redis": { "available": false, "reason": "not_configured" },
    "storage": { "writable": true, "path": "/data/archive" }
  }
}
```

Notes:
- `version` comes from installed package metadata; fallback may be `0.0.0` in some non-packaged contexts.
- When `HEALTHZ_OMIT_VERSION=true`, the response contains only `status` and `time` (no `service` or `version`).
- The `checks` object is only present when `deep=true`.

### `GET /metrics`

Only mounted when `observability.metrics_enabled=true`. When `METRICS_BEARER_TOKEN` is set, requests must include `Authorization: Bearer <token>`; otherwise `401` is returned.

Response format:
- Prometheus text exposition (`text/plain`)

### `GET /admin`

Returns a lightweight admin dashboard HTML shell.

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

#### Query parameters

- `limit` (int, optional, default `10`, max `1000`)

#### Success response

- status: `200`
- body:

```json
{
  "status": "ok",
  "replayed": 5
}
```

#### Error responses

- `401` missing/invalid bearer token
- `404` admin disabled
- `503` admin token not configured or DLQ backend unavailable

### `GET /admin/api/config/check`

Returns configuration validation status, including whether the current settings pass all validation checks and the state of key runtime dependencies.

#### Success response

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

#### Error responses

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

Secret sources:
- preferred: `zammad.webhook_hmac_secret` (`WEBHOOK_HMAC_SECRET`)
- legacy fallback: `server.webhook_shared_secret` (`WEBHOOK_SHARED_SECRET`)

### Unsigned mode

Default is fail-closed.

To allow unsigned requests (internal testing only):
- `hardening.webhook.allow_unsigned=true`

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
