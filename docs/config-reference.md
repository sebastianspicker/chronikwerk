# Configuration Reference

Source of truth:
- `src/zammad_pdf_archiver/config/settings.py`
- `src/zammad_pdf_archiver/config/load.py`
- `src/zammad_pdf_archiver/config/validate.py`

## 1. Load and Precedence

Effective precedence (highest first):
1. environment variables (including `.env` values loaded into process env)
2. YAML mapping (`CONFIG_PATH` or `config/config.yaml` when present)
3. defaults in settings model

Notes:
- nested env keys use `__`, example: `ZAMMAD__BASE_URL`
- `.env` is loaded with `override=false`
- if `CONFIG_PATH` is set and file is missing, startup fails

## 2. Minimum Required Configuration

Required unless overridden by explicit unsafe/test options:
- `zammad.base_url`
- `zammad.api_token`
- `storage.root`

## 3. Key Reference

### `server`

| Key | Default | Env key | Description |
|---|---|---|---|
| `server.host` | `0.0.0.0` | `SERVER__HOST` | bind host |
| `server.port` | `8080` | `SERVER__PORT` | bind port |

### `zammad`

| Key | Default | Env key | Description |
|---|---|---|---|
| `zammad.base_url` | required | `ZAMMAD__BASE_URL` | Zammad base URL |
| `zammad.api_token` | required | `ZAMMAD__API_TOKEN` | API token |
| `zammad.webhook_hmac_secret` | `null` | `ZAMMAD__WEBHOOK_HMAC_SECRET` | webhook HMAC secret |
| `zammad.timeout_seconds` | `10.0` | `ZAMMAD__TIMEOUT_SECONDS` | outbound timeout |
| `zammad.verify_tls` | `true` | `ZAMMAD__VERIFY_TLS` | verify upstream TLS certs |

### `workflow`

| Key | Default | Env key | Description |
|---|---|---|---|
| `workflow.trigger_tag` | `pdf:sign` | `WORKFLOW__TRIGGER_TAG` | trigger tag |
| `workflow.require_tag` | `true` | `WORKFLOW__REQUIRE_TAG` | require trigger tag for processing |
| `workflow.acknowledge_on_success` | `true` | none | create success note on ticket |
| `workflow.delivery_id_ttl_seconds` | `3600` | `WORKFLOW__DELIVERY_ID_TTL_SECONDS` | in-memory dedupe TTL |

### `fields`

| Key | Default | Env key | Description |
|---|---|---|---|
| `fields.archive_path` | `archive_path` | `FIELDS__ARCHIVE_PATH` | ticket custom field name for archive path |
| `fields.archive_user_mode` | `archive_user_mode` | `FIELDS__ARCHIVE_USER_MODE` | ticket custom field name for user mode |
| `fields.archive_user` | `archive_user` | `FIELDS__ARCHIVE_USER` | ticket custom field name for fixed user (when mode is `fixed`) |

### `storage`

| Key | Default | Env key | Description |
|---|---|---|---|
| `storage.root` | required | `STORAGE__ROOT` | storage root path |
| `storage.fsync` | `true` | `STORAGE__FSYNC` | file/dir fsync behavior |

| `storage.filename_pattern` | `Ticket-{ticket_number}_{timestamp_utc}.pdf` | none | output filename template |

### `pdf`

| Key | Default | Env key | Description |
|---|---|---|---|
| `pdf.locale` | `de_DE` | `PDF__LOCALE` | locale setting (template-dependent) |
| `pdf.timezone` | `Europe/Berlin` | `PDF__TIMEZONE` | timezone setting (template-dependent) |
| `pdf.max_articles` | `250` | `PDF__MAX_ARTICLES` | max article count (`0` disables limit) |
| `pdf.article_limit_mode` | `fail` | `PDF__ARTICLE_LIMIT_MODE` | `fail` (raise when over limit) or `cap_and_continue` (truncate and warn) |
| `pdf.max_total_attachment_bytes` | `52428800` | `PDF__MAX_TOTAL_ATTACHMENT_BYTES` | max total attachment bytes per ticket |

### `signing`

| Key | Default | Env key | Description |
|---|---|---|---|
| `signing.enabled` | `false` | `SIGNING__ENABLED` | enable signing flow |
| `signing.pfx_path` | `null` | `SIGNING__PFX_PATH` | PKCS#12/PFX path |
| `signing.pfx_password` | `null` | `SIGNING__PFX_PASSWORD` | PFX password |

#### `signing.pades`

| Key | Default | Env key | Description |
|---|---|---|---|
| `signing.pades.reason` | `Ticket Archivierung` | `SIGNING__PADES__REASON` | PDF signature reason |
| `signing.pades.location` | `Datacenter` | `SIGNING__PADES__LOCATION` | PDF signature location |

#### `signing.timestamp.rfc3161`

| Key | Default | Env key | Description |
|---|---|---|---|
| `signing.timestamp.enabled` | `false` | `SIGNING__TIMESTAMP__ENABLED` | enable RFC3161 timestamping |
| `signing.timestamp.rfc3161.tsa_url` | `null` | `SIGNING__TIMESTAMP__RFC3161__TSA_URL` | TSA endpoint URL |
| `signing.timestamp.rfc3161.timeout_seconds` | `10.0` | `SIGNING__TIMESTAMP__RFC3161__TIMEOUT_SECONDS` | TSA timeout |
| `signing.timestamp.rfc3161.ca_bundle_path` | `null` | `SIGNING__TIMESTAMP__RFC3161__CA_BUNDLE_PATH` | custom trust bundle path |
| `signing.timestamp.rfc3161.user` | `null` | `SIGNING__TIMESTAMP__RFC3161__USER` | TSA HTTP basic auth username |
| `signing.timestamp.rfc3161.password` | `null` | `SIGNING__TIMESTAMP__RFC3161__PASSWORD` | TSA HTTP basic auth password (SecretStr) |

### `observability`

| Key | Default | Env key | Description |
|---|---|---|---|
| `observability.log_level` | `INFO` | `OBSERVABILITY__LOG_LEVEL` | log level |
| `observability.log_format` | `null` | `OBSERVABILITY__LOG_FORMAT` | `json` or `human` |
| `observability.metrics_enabled` | `false` | `OBSERVABILITY__METRICS_ENABLED` | expose `/metrics` |
| `observability.metrics_bearer_token` | `null` | `OBSERVABILITY__METRICS_BEARER_TOKEN` | when set, require `Authorization: Bearer <token>` for `/metrics` |
| `observability.healthz_omit_version` | `false` | `OBSERVABILITY__HEALTHZ_OMIT_VERSION` | omit `version` and `service` from `/healthz` response |

### `hardening.rate_limit`

| Key | Default | Env key | Description |
|---|---|---|---|
| `hardening.rate_limit.enabled` | `true` | `HARDENING__RATE_LIMIT__ENABLED` | enable rate limit middleware |
| `hardening.rate_limit.rps` | `5.0` | `HARDENING__RATE_LIMIT__RPS` | token refill rate |
| `hardening.rate_limit.burst` | `10` | `HARDENING__RATE_LIMIT__BURST` | token bucket capacity |
| `hardening.rate_limit.include_metrics` | `false` | `HARDENING__RATE_LIMIT__INCLUDE_METRICS` | include `/metrics` path |
| `hardening.rate_limit.client_key_header` | `null` | `HARDENING__RATE_LIMIT__CLIENT_KEY_HEADER` | header for rate-limit key (e.g. `X-Forwarded-For`) when behind proxy |

### `hardening.body_size_limit`

| Key | Default | Env key | Description |
|---|---|---|---|
| `hardening.body_size_limit.max_bytes` | `1048576` | `HARDENING__BODY_SIZE_LIMIT__MAX_BYTES` | max request body bytes (`0` disables) |

### `hardening.webhook`

| Key | Default | Env key | Description |
|---|---|---|---|
| `hardening.webhook.require_delivery_id` | `false` | `HARDENING__WEBHOOK__REQUIRE_DELIVERY_ID` | require `X-Zammad-Delivery` header |

### `hardening.transport`

| Key | Default | Env key | Description |
|---|---|---|---|
| `hardening.transport.trust_env` | `false` | `HARDENING__TRANSPORT__TRUST_ENV` | allow proxy env for outbound HTTP |

| Key | Default | Env key | Description |
|---|---|---|---|
| `retry_bearer_token` | `null` | `RETRY_BEARER_TOKEN` | Bearer auth token for `POST /retry/{ticket_id}` |

## 4. Non-schema Runtime Environment Keys

These are used by runtime/deployment but not part of `Settings` model:
- `CONFIG_PATH` (YAML config path)

## 5. Nested Environment Examples

Equivalent nested env keys:

```bash
ZAMMAD__BASE_URL=https://zammad.example.local
ZAMMAD__API_TOKEN=CHANGE-ME
STORAGE__ROOT=/mnt/archive
```

## 6. Minimal Config Examples

### Minimal YAML

```yaml
zammad:
  base_url: "https://zammad.example.local"
  api_token: "CHANGE-ME"
  webhook_hmac_secret: "CHANGE-ME"
storage:
  root: "/mnt/archive"
```

### Minimal Env

```bash
ZAMMAD__BASE_URL=https://zammad.example.local
ZAMMAD__API_TOKEN=CHANGE-ME
ZAMMAD__WEBHOOK_HMAC_SECRET=CHANGE-ME
STORAGE__ROOT=/mnt/archive
```
