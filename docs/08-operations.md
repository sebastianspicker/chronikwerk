# 08 - Operations

Canonical runbook for starting, observing, troubleshooting, and recovering the
service. Deployment preparation lives in [deploy.md](deploy.md).

## Endpoint Semantics

- `POST /ingest`: returns `202` after accepting one payload; processing runs in
  the background.
- `POST /ingest/batch`: returns `202` after accepting a batch; each payload is
  scheduled separately.
- `POST /retry/{ticket_id}`: returns `202` after accepting one forced retry.
- `GET /jobs/history`: returns authenticated, process-local history.
- `GET /healthz`: liveness/status, with optional `?deep=true` storage check.
- `GET /metrics`: Prometheus metrics when enabled.

`202` means accepted, not archived. Confirm completion by checking final tags,
the internal ticket note, logs, and archive output.

When the bounded in-process admission limit is full, ingest returns `503` with
`code=job_capacity_exhausted` and `Retry-After: 1`; no background task was
accepted. Batch requests are rejected as a whole when their jobs do not fit.

## Start and Stop

```bash
sudo docker compose --env-file /etc/zammad-archiver/zammad-archiver.env up -d --build
sudo docker compose --env-file /etc/zammad-archiver/zammad-archiver.env ps
sudo docker compose --env-file /etc/zammad-archiver/zammad-archiver.env logs -f
sudo docker compose --env-file /etc/zammad-archiver/zammad-archiver.env down
```

Health check:

```bash
curl -fsS "http://127.0.0.1:${SERVER__PORT:-8080}/healthz"
curl -fsS "http://127.0.0.1:${SERVER__PORT:-8080}/healthz?deep=true"
```

## Update and Rollback

Update:

```bash
cd /opt/zammad-ticket-archiver
sudo rsync -a --delete /path/to/updated/repo/ ./
sudo docker compose --env-file /etc/zammad-archiver/zammad-archiver.env up -d --build
```

Rollback:

```bash
cd /opt/zammad-ticket-archiver
sudo git checkout <known-good-commit-or-tag>
sudo docker compose --env-file /etc/zammad-archiver/zammad-archiver.env up -d --build
```

For no-build rollbacks, publish versioned images and pin compose `image:` tags.

## Optional systemd Wrapper

```bash
sudo install -m 0644 infra/systemd/zammad-archiver.service /etc/systemd/system/zammad-archiver.service
sudo systemctl daemon-reload
sudo systemctl enable --now zammad-archiver.service
```

The unit assumes `/opt/zammad-ticket-archiver`. Adjust `WorkingDirectory=` if
your deployment path differs.

```bash
sudo systemctl status zammad-archiver.service
sudo journalctl -u zammad-archiver.service -f
```

## Observability

Primary signals:

- structured service logs (`request_id`, `ticket_id`, optional `delivery_id`)
- ticket internal notes
- ticket tags (`pdf:sign`, `pdf:processing`, `pdf:signed`, `pdf:error`)
- `GET /jobs/history`
- optional Prometheus metrics

## Idempotency and Retry Limits

The default runtime is single-instance and process-local:

- Graceful shutdown waits for admitted work, but a process crash or abrupt
  termination can lose accepted background work.
- In-flight ticket locks are process-local.
- Delivery-ID dedupe is in-memory and resets on restart.
- A delivery ID is claimed before processing completes, so retry with a new
  Zammad delivery or wait for the TTL after failures.

Use one service instance unless you have verified the concurrency and storage
semantics for your deployment.

Internal success/error note creation is a non-idempotent Zammad `POST` and is
attempted once per processing pass; transport and 5xx failures are not retried
automatically to avoid duplicate notes. Re-run the ticket through the workflow
or `POST /retry/{ticket_id}` after resolving the cause.

## Reprocessing Workflow

1. Read the latest archiver ticket note and classification.
2. Fix the root cause: storage, credentials, network, signing, TSA, or payload.
3. Remove stale `pdf:processing` if present.
4. Ensure the trigger tag is present unless using `POST /retry/{ticket_id}`.
5. Remove `pdf:signed` only when you intentionally want a new archive output.
6. Trigger a fresh Zammad update or call `POST /retry/{ticket_id}`.
7. Confirm `pdf:signed` or a new `pdf:error` note.

## Troubleshooting

### `403 forbidden` on `/ingest`

Check:

- matching `ZAMMAD__WEBHOOK_HMAC_SECRET`
- `X-Hub-Signature` header is present
- proxy does not transform the request body after signing

### `503 webhook_auth_not_configured`

Set `ZAMMAD__WEBHOOK_HMAC_SECRET` for signed mode.

### `400 missing_delivery_id`

`hardening.webhook.require_delivery_id=true` is enabled and the request lacks
`X-Zammad-Delivery`.

### Ticket ends in `pdf:error`

Check:

- storage mount path, permissions, free space, and quota
- Zammad API token permissions
- signing PFX path/password
- TSA URL, credentials, and trust
- `pdf.max_articles` and attachment limits for large tickets

### Ticket remains `pdf:processing`

The process may have exited during background work. Inspect logs and
`/jobs/history`, then run the reprocessing workflow above.

## On-Call Fast Triage

1. Did `/ingest` return `202`?
2. What is the current ticket tag state?
3. What does the latest internal note say?
4. Is the expected destination path writable?
5. Could delivery-ID dedupe have skipped the replay?
6. Does `GET /healthz?deep=true` report writable storage?

## Release Safety Reminders

- Protect `/metrics` with a bearer token or network policy when enabled.
- Treat CIFS/SMB durability as a storage-system contract, not an app guarantee.
- Validate signing and timestamp trust in the target environment.
- Run [release-checklist.md](release-checklist.md) before publication.
