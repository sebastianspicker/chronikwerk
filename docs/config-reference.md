# Configuration Reference

Source of truth:

- `src/zammad_pdf_archiver/config/settings.py`
- `src/zammad_pdf_archiver/config/load.py`
- `src/zammad_pdf_archiver/config/validate.py`

## Load Precedence

Highest first:

1. Environment variables, including values loaded from `.env`.
2. YAML mapping from `CONFIG_PATH`, or `config/config.yaml` when present.
3. Defaults in the settings model.

Nested environment keys use double underscores, for example
`ZAMMAD__BASE_URL`.

## Minimum Required Values

Production-like runs must provide:

- `zammad.base_url` / `ZAMMAD__BASE_URL`
- `zammad.api_token` / `ZAMMAD__API_TOKEN`
- `storage.root` / `STORAGE__ROOT`
- `zammad.webhook_hmac_secret` / `ZAMMAD__WEBHOOK_HMAC_SECRET` for signed
  webhook mode

## Server

| Key | Default | Env key | Description |
| --- | --- | --- | --- |
| `server.host` | `0.0.0.0` | `SERVER__HOST` | Bind host. |
| `server.port` | `8080` | `SERVER__PORT` | Bind port. |

## Zammad

| Key | Default | Env key | Description |
| --- | --- | --- | --- |
| `zammad.base_url` | required | `ZAMMAD__BASE_URL` | Zammad base URL. |
| `zammad.api_token` | required | `ZAMMAD__API_TOKEN` | Zammad API token. |
| `zammad.webhook_hmac_secret` | `null` | `ZAMMAD__WEBHOOK_HMAC_SECRET` | HMAC secret for incoming webhooks. |
| `zammad.timeout_seconds` | `10.0` | `ZAMMAD__TIMEOUT_SECONDS` | Outbound API timeout. |
| `zammad.verify_tls` | `true` | `ZAMMAD__VERIFY_TLS` | Verify upstream TLS certificates. |

## Workflow

| Key | Default | Env key | Description |
| --- | --- | --- | --- |
| `workflow.trigger_tag` | `pdf:sign` | `WORKFLOW__TRIGGER_TAG` | Tag that requests archiving. |
| `workflow.require_tag` | `true` | `WORKFLOW__REQUIRE_TAG` | Require the trigger tag before processing. |
| `workflow.acknowledge_on_success` | `true` | `WORKFLOW__ACKNOWLEDGE_ON_SUCCESS` | Write a success note after archiving. |
| `workflow.delivery_id_ttl_seconds` | `3600` | `WORKFLOW__DELIVERY_ID_TTL_SECONDS` | In-memory dedupe TTL for `X-Zammad-Delivery`. |

## Fields

| Key | Default | Env key | Description |
| --- | --- | --- | --- |
| `fields.archive_path` | `archive_path` | `FIELDS__ARCHIVE_PATH` | Ticket field containing archive path segments. |
| `fields.archive_user_mode` | `archive_user_mode` | `FIELDS__ARCHIVE_USER_MODE` | Ticket field selecting user directory mode. |
| `fields.archive_user` | `archive_user` | `FIELDS__ARCHIVE_USER` | Ticket field used when mode is `fixed`. |

## Storage

| Key | Default | Env key | Description |
| --- | --- | --- | --- |
| `storage.root` | required | `STORAGE__ROOT` | Root directory for archive output. |
| `storage.fsync` | `true` | `STORAGE__FSYNC` | Fsync files/directories after writes. |
| `storage.filename_pattern` | `Ticket-{ticket_number}_{timestamp_utc}.pdf` | `STORAGE__FILENAME_PATTERN` | Output PDF filename template. |

## PDF

| Key | Default | Env key | Description |
| --- | --- | --- | --- |
| `pdf.locale` | `de_DE` | `PDF__LOCALE` | Locale used by templates. |
| `pdf.timezone` | `Europe/Berlin` | `PDF__TIMEZONE` | Time zone used by templates. |
| `pdf.max_articles` | `250` | `PDF__MAX_ARTICLES` | Maximum article count; `0` disables the limit. |
| `pdf.article_limit_mode` | `fail` | `PDF__ARTICLE_LIMIT_MODE` | `fail` or `cap_and_continue`. |
| `pdf.max_total_attachment_bytes` | `52428800` | `PDF__MAX_TOTAL_ATTACHMENT_BYTES` | Maximum attachment bytes per ticket. |

## Signing

| Key | Default | Env key | Description |
| --- | --- | --- | --- |
| `signing.enabled` | `false` | `SIGNING__ENABLED` | Enable PDF signing. |
| `signing.pfx_path` | `null` | `SIGNING__PFX_PATH` | PKCS#12/PFX bundle path. |
| `signing.pfx_password` | `null` | `SIGNING__PFX_PASSWORD` | PFX password. |
| `signing.pades.reason` | `Ticket Archivierung` | `SIGNING__PADES__REASON` | Signature reason. |
| `signing.pades.location` | `Datacenter` | `SIGNING__PADES__LOCATION` | Signature location. |
| `signing.timestamp.enabled` | `false` | `SIGNING__TIMESTAMP__ENABLED` | Enable RFC3161 timestamping. |
| `signing.timestamp.rfc3161.tsa_url` | `null` | `SIGNING__TIMESTAMP__RFC3161__TSA_URL` | TSA endpoint. |
| `signing.timestamp.rfc3161.user` | `null` | `SIGNING__TIMESTAMP__RFC3161__USER` | TSA basic-auth user. |
| `signing.timestamp.rfc3161.password` | `null` | `SIGNING__TIMESTAMP__RFC3161__PASSWORD` | TSA basic-auth password. |
| `signing.timestamp.rfc3161.timeout_seconds` | `10.0` | `SIGNING__TIMESTAMP__RFC3161__TIMEOUT_SECONDS` | TSA request timeout. |

## Observability

| Key | Default | Env key | Description |
| --- | --- | --- | --- |
| `observability.log_level` | `INFO` | `OBSERVABILITY__LOG_LEVEL` | Log level. |
| `observability.log_format` | `null` | `OBSERVABILITY__LOG_FORMAT` | `json` or `human`. |
| `observability.metrics_enabled` | `false` | `OBSERVABILITY__METRICS_ENABLED` | Mount `/metrics`. |
| `observability.metrics_bearer_token` | `null` | `OBSERVABILITY__METRICS_BEARER_TOKEN` | Bearer token for `/metrics`. |
| `observability.healthz_omit_version` | `false` | `OBSERVABILITY__HEALTHZ_OMIT_VERSION` | Omit service/version from `/healthz`. |

## Hardening

| Key | Default | Env key | Description |
| --- | --- | --- | --- |
| `hardening.rate_limit.enabled` | `true` | `HARDENING__RATE_LIMIT__ENABLED` | Enable token-bucket rate limiting. |
| `hardening.rate_limit.rps` | `5.0` | `HARDENING__RATE_LIMIT__RPS` | Refill rate. |
| `hardening.rate_limit.burst` | `10` | `HARDENING__RATE_LIMIT__BURST` | Burst capacity. |
| `hardening.rate_limit.include_metrics` | `false` | `HARDENING__RATE_LIMIT__INCLUDE_METRICS` | Include `/metrics` in rate limiting. |
| `hardening.rate_limit.client_key_header` | `null` | `HARDENING__RATE_LIMIT__CLIENT_KEY_HEADER` | Trusted header for client key behind a proxy. |
| `hardening.body_size_limit.max_bytes` | `1048576` | `HARDENING__BODY_SIZE_LIMIT__MAX_BYTES` | Request body limit; `0` disables. |
| `hardening.webhook.require_delivery_id` | `false` | `HARDENING__WEBHOOK__REQUIRE_DELIVERY_ID` | Require `X-Zammad-Delivery`. |
| `hardening.transport.trust_env` | `false` | `HARDENING__TRANSPORT__TRUST_ENV` | Allow proxy env vars for outbound HTTP. |

## Top-Level Runtime Tokens

| Key | Default | Env key | Description |
| --- | --- | --- | --- |
| `retry_bearer_token` | `null` | `RETRY_BEARER_TOKEN` | Bearer token for `POST /retry/{ticket_id}`. |

## Minimal YAML

```yaml
zammad:
  base_url: "https://zammad.example.local"
  api_token: "CHANGE-ME"
  webhook_hmac_secret: "CHANGE-ME"
storage:
  root: "/mnt/archive"
```

## Minimal Environment

```bash
ZAMMAD__BASE_URL=https://zammad.example.local
ZAMMAD__API_TOKEN=CHANGE-ME
ZAMMAD__WEBHOOK_HMAC_SECRET=CHANGE-ME
STORAGE__ROOT=/mnt/archive
```
