# API Reference

This document describes the HTTP contract exposed by `zammad-pdf-archiver`.

## `POST /ingest`

Accepts one Zammad webhook payload and schedules background processing.

Query parameters:

- `dry_run` (bool, default `false`): validate the payload and return `202`
  without scheduling work.

Headers:

- `Content-Type: application/json`
- `X-Hub-Signature`: required when webhook HMAC verification is configured.
- `X-Zammad-Delivery`: required only when
  `hardening.webhook.require_delivery_id=true`.
- `X-Request-Id`: optional request correlation ID.

Body:

- JSON object containing either `ticket.id` or `ticket_id`.

Success:

```json
{"status":"accepted","ticket_id":123}
```

Dry run success:

```json
{"status":"dry_run_accepted","ticket_id":123}
```

Common errors:

| Status | Detail/code | Meaning |
| --- | --- | --- |
| `400` | `missing_delivery_id` | Strict delivery-ID mode is enabled and the header is missing. |
| `403` | `forbidden` | HMAC validation failed. |
| `413` | `request_too_large` | Request body exceeds the configured limit. |
| `422` | validation error | Payload does not contain a positive ticket ID. |
| `429` | `rate_limited` | Rate limit exceeded. |
| `503` | `webhook_auth_not_configured` | Signed mode is required but no webhook secret is configured. |

## `POST /ingest/batch`

Accepts a JSON array of webhook payloads and schedules one job per item.

Limits:

- Maximum batch size: `100`.
- Each item must contain either `ticket.id` or `ticket_id`.

Headers and error behavior match `POST /ingest`. When a batch-level
`X-Zammad-Delivery` header is present, per-item delivery IDs are derived as
`<delivery-id>:<index>`.

Success:

```json
{"status":"accepted","count":2}
```

Dry run success:

```json
{"status":"dry_run_accepted","count":2}
```

## `POST /retry/{ticket_id}`

Forces one reprocessing attempt for a ticket.

Headers:

- `Authorization: Bearer <RETRY_BEARER_TOKEN>`

Success:

```json
{"status":"accepted","ticket_id":123}
```

Common errors:

- `401`: missing or invalid bearer token.
- `503`: retry token or settings are not configured.

### `GET /jobs/history`

Returns process-local processing history when explicitly enabled. The route is
disabled by default and requires `Authorization: Bearer <OBSERVABILITY__HISTORY_BEARER_TOKEN>`.

Query parameters:

- `limit` (default `100`, max enforced by runtime history store)
- `ticket_id` (optional integer filter)

Example response:

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

## `GET /healthz`

Returns shallow service health by default.

Query parameters:

- `deep` (bool, default `false`): include a storage writability check.

Example:

```json
{
  "status": "ok",
  "service": "zammad-pdf-archiver",
  "version": "0.2.0rc2",
  "time": "2026-02-07T12:00:00+00:00"
}
```

When `OBSERVABILITY__HEALTHZ_OMIT_VERSION=true`, the response omits `service`
and `version`.

## `GET /metrics`

Mounted only when `observability.metrics_enabled=true`; configuration also
requires a non-blank bearer token.

Authentication:

- `OBSERVABILITY__METRICS_BEARER_TOKEN` is required when metrics are enabled;
  requests must include `Authorization: Bearer <token>`.

Response format: Prometheus text exposition.

## Administration application and API

All `/admin` routes are absent (`404`) unless `admin.enabled=true`. Admin responses use
`Cache-Control: no-store`, a strict same-origin CSP, frame denial, `nosniff`, and
`Referrer-Policy: no-referrer`. Login exchanges the external access token for an
`HttpOnly`, `SameSite=Strict`, secure-by-default process-local session cookie. Every
state-changing request requires the per-session `X-CSRF-Token`.

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/admin/api/v1/session` | Create a process-local admin session; returns `204`. |
| `DELETE` | `/admin/api/v1/session` | CSRF-protected logout. |
| `GET` | `/admin/api/v1/status` | Runtime, admission, volatile-history, and revision state. |
| `POST` | `/admin/api/v1/status/storage-check` | User-initiated deep storage check. |
| `GET` | `/admin/api/v1/jobs` | Cursor-paginated process-local history (`limit<=100`). |
| `POST` | `/admin/api/v1/jobs/{ticket_id}/retry` | Acknowledged retry request; returns `202`. |
| `GET` | `/admin/api/v1/config` | Allowlisted values, ownership, secret-presence booleans, revisions. |
| `POST` | `/admin/api/v1/config/validate` | Validate a flat field overlay and return a normalized diff. |
| `PUT` | `/admin/api/v1/config/staged` | Stage with `If-Match`; restart remains external. |
| `GET` | `/admin/api/v1/config/revisions` | Bounded non-secret revision metadata. |
| `POST` | `/admin/api/v1/config/revisions/{revision}/restore` | Stage a previous overlay as a new revision. |

Admin API errors use stable `code`, localized `message`, and `request_id` fields. Machine
status values such as `accepted`, `processed`, and `failed` are never localized.

## Webhook HMAC

Supported signature header:

- `X-Hub-Signature`

Supported algorithm:

- `sha256=<hex>`

The signature is computed over the raw request body bytes. SHA-1 compatibility
is not supported.

Example:

```bash
curl -i \
  -H "Content-Type: application/json" \
  -H "X-Hub-Signature: sha256=$hex_signature" \
  -H "X-Zammad-Delivery: delivery-001" \
  --data-binary @examples/webhook-payload.sample.json \
  http://127.0.0.1:8080/ingest
```

Unsigned webhook mode is for local/internal testing only. Production deployments
should configure `ZAMMAD__WEBHOOK_HMAC_SECRET`.
