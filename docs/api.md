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

#### Request headers

- `Content-Type: application/json` (recommended)
- `X-Request-Id: <id>` (optional)
- `X-Zammad-Delivery: <id>` (required only when `hardening.webhook.require_delivery_id=true`)

#### Request body

JSON array of ingest payload objects (maximum **100** items per request). Each item must contain either:
- `ticket.id`
- `ticket_id`

When `X-Zammad-Delivery` is present, the service derives per-item delivery IDs as `<delivery-id>:<index>` (zero-based) before applying idempotency checks.

#### Success response

- status: `202`
- body: `{"status":"accepted","count":<int>}`
- header: `X-Request-Id` is always returned

#### Error responses

- `400` `{"detail":"missing_delivery_id"}`
- `403` `{"detail":"forbidden"}`
- `422` invalid body (e.g. missing or invalid ticket id in an item), or batch exceeds 100 items (`{"detail":"batch_too_large"}`)
- `413` `{"detail":"request_too_large"}`
- `429` `{"detail":"rate_limited"}`
- `503` `{"detail":"webhook_auth_not_configured"}` or `{"detail":"shutting_down"}`

### `POST /retry/{ticket_id}`

Schedules one forced reprocessing run for a specific ticket ID.
Requires `Authorization: Bearer <ADMIN_BEARER_TOKEN>`.

Behavior notes:
- bypasses trigger-tag gating
- bypasses `pdf:signed` skip behavior
- still skips delivery-ID dedupe by using no delivery ID

#### Request headers

- `Authorization: Bearer <ADMIN_BEARER_TOKEN>` (required)

#### Path parameters

- `ticket_id` (int, required)

#### Success response

- status: `202`
- body: `{"status":"accepted","ticket_id":<int>}`

#### Error responses

- `401` missing/invalid bearer token
- `503` `{"detail":"retry_token_not_configured"}` or `{"detail":"settings_not_configured"}`



### `GET /jobs/history`

Query parameters:
- `limit` (optional, default `100`, max `5000`)
- `ticket_id` (optional int filter)

Response:

```json
{
  "entries": [
    {
      "id": "1",
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

### `GET /healthz`

Always available.

#### Query parameters


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
    "storage": { "writable": true }
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
    "storage": { "writable": true }
  }
}
```

Notes:
- `version` comes from installed package metadata; fallback may be `0.0.0` in some non-packaged contexts.
- When `OBSERVABILITY__HEALTHZ_OMIT_VERSION=true`, the response contains only `status` and `time` (no `service` or `version`).
- The `checks` object is only present when `deep=true`.

### `GET /metrics`

Only mounted when `observability.metrics_enabled=true`. When `OBSERVABILITY__METRICS_BEARER_TOKEN` is set, requests must include `Authorization: Bearer <token>`; otherwise `401` is returned.

Response format:
- Prometheus text exposition (`text/plain`)

## 2. Webhook Security Contract

### HMAC verification

When a secret is configured:
- header: `X-Hub-Signature`
- algorithms: HMAC-SHA1 and HMAC-SHA256 (sender chooses; prefer SHA-256 for new setups)
- message: raw request body bytes

Secret sources:
- preferred: `zammad.webhook_hmac_secret` (`ZAMMAD__WEBHOOK_HMAC_SECRET`)
- webhook secret: `zammad.webhook_hmac_secret`

### Unsigned mode

Default is fail-closed.

To allow unsigned requests (internal testing only):

### Delivery ID requirement

Optional strict mode:
- set `hardening.webhook.require_delivery_id=true`
- then `X-Zammad-Delivery` is mandatory

## 3. Idempotency Contract

`X-Zammad-Delivery` is used for best-effort dedupe:
- duplicate delivery IDs are skipped for `workflow.delivery_id_ttl_seconds`
- dedupe state is in-memory by default and not durable across restarts

## 4. Example Signed Request

SHA-1 (Zammad typically sends this):

```bash
curl -i \
  -H "Content-Type: application/json" \
  -H "X-Hub-Signature: $sig" \
  -H "X-Zammad-Delivery: delivery-001" \
  --data-binary @payload.json \
  http://127.0.0.1:8080/ingest
```

SHA-256 is also accepted: use `sha256=<hex>` in the header and compute HMAC-SHA256 over the raw body.
