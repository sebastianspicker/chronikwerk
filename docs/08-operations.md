# Operations

Runbook for starting, observing, troubleshooting, and recovering the service. Deployment
preparation lives in [deploy.md](deploy.md).

## Endpoint semantics

- `POST /ingest` returns `202` after accepting one payload; processing runs in the background.
- `POST /ingest/batch` returns `202` after accepting a batch; each payload is scheduled separately.
- `POST /retry/{ticket_id}` returns `202` after accepting one forced retry.
- `GET /jobs/history` returns authenticated, process-local history.
- `GET /healthz` reports liveness/status, with an optional `?deep=true` storage check.
- `GET /metrics` exposes Prometheus metrics when enabled.
- The admin application is an optional, session-authenticated surface for overview, volatile
  history, retry, and staged non-secret configuration.

`202` means accepted, not archived. Confirm completion from the final tags, the internal ticket
note, the logs, and the archive output.

When the bounded in-process admission limit is full, ingest returns `503` with
`code=job_capacity_exhausted` and `Retry-After: 1`, and no background task was accepted. Batch
requests are rejected as a whole when their jobs do not fit.

## Start and stop

```bash
sudo docker compose --env-file /etc/chronikwerk/chronikwerk.env up -d --build
sudo docker compose --env-file /etc/chronikwerk/chronikwerk.env ps
sudo docker compose --env-file /etc/chronikwerk/chronikwerk.env logs -f
sudo docker compose --env-file /etc/chronikwerk/chronikwerk.env down
```

Shutdown stops accepting work and closes admission before awaiting tracked jobs. After the configured grace period, jobs
receive cancellation; blocking normalization, rendering, signing, and filesystem work still finishes
before its job releases resources. The shared Zammad client closes after those finalizers, so total
shutdown can exceed the grace period.

Health check:

```bash
sudo docker compose --env-file /etc/chronikwerk/chronikwerk.env exec -T chronikwerk \
  python - <<'PY'
import os
import urllib.request

port = os.getenv("SERVER__PORT", "8080")
for path in ("/healthz", "/healthz?deep=true"):
    response = urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=2)
    print(path, response.status)
PY
```

## Update and rollback

Update:

```bash
cd /opt/chronikwerk
sudo git fetch --tags --prune
sudo git checkout <new-release-tag>
sudo docker compose --env-file /etc/chronikwerk/chronikwerk.env up -d --build
```

Update from a clean tag or a versioned image. Never sync a developer working tree into `/opt`:
ignored local configuration, credentials, archives, evidence, and tool state must stay outside the
deployment source tree.

Rollback:

```bash
cd /opt/chronikwerk
sudo git checkout <known-good-commit-or-tag>
sudo docker compose --env-file /etc/chronikwerk/chronikwerk.env up -d --build
```

For no-build rollbacks, publish versioned images and pin compose `image:` tags.

Managed configuration stays restart-only. The UI labels a newly staged revision as inactive until
the process is restarted externally. If a staged revision prevents web startup, use the offline CLI
against the same external configuration and state directory:

```bash
chronikwerk-admin list-config-revisions
chronikwerk-admin stage-config-rollback <full-revision-hash>
sudo docker compose --env-file /etc/chronikwerk/chronikwerk.env restart
```

## Optional systemd wrapper

```bash
sudo install -m 0644 infra/systemd/chronikwerk.service /etc/systemd/system/chronikwerk.service
sudo systemctl daemon-reload
sudo systemctl enable --now chronikwerk.service
```

The unit assumes `/opt/chronikwerk`; adjust `WorkingDirectory=` if your deployment path differs.

```bash
sudo systemctl status chronikwerk.service
sudo journalctl -u chronikwerk.service -f
```

## Observability

Primary signals:

- structured service logs (`request_id`, `ticket_id`, optional `delivery_id`)
- ticket internal notes
- ticket tags (`pdf:sign`, `pdf:processing`, `pdf:signed`, `pdf:error`)
- `GET /jobs/history`
- optional Prometheus metrics (see below)

Prometheus metrics, when enabled:

| Metric | Type | Meaning |
| --- | --- | --- |
| `processed_total` | counter | Tickets archived successfully. |
| `skipped_total{reason}` | counter | Skipped attempts; `reason` is `not_triggered`, `in_flight`, or `idempotency`. |
| `failed_total` | counter | Failed attempts. |
| `render_seconds`, `sign_seconds`, `total_seconds` | histogram | PDF rendering, signing, and end-to-end processing time. |
| `admission_pending`, `admission_running` | gauge | Admitted jobs waiting for a slot and currently running. |
| `admission_rejected_total` | counter | Jobs rejected because admission capacity was exhausted. |

Treat logs and internal ticket notes as sensitive operational data — they can contain delivery
identifiers and absolute archive paths.

## Idempotency and retry limits

The default runtime is single-instance and process-local:

- Graceful shutdown gives admitted work `admission.shutdown_timeout_seconds` to drain before async
  cancellation. In-flight PDF, signing, and filesystem worker threads cannot be stopped safely, so
  the service waits for them after cancellation and total shutdown can exceed the configured grace
  period. A process crash or abrupt termination can lose accepted background work.
- In-flight ticket locks are process-local.
- Delivery-ID dedupe is in-memory and resets on restart.
- A delivery ID is claimed before processing completes, so retry with a new Zammad delivery or wait
  for the TTL after a failure.

Use one service instance unless you have verified the concurrency and storage semantics for your
deployment.

Internal success/error notes are a non-idempotent Zammad `POST`, attempted once per processing pass;
transport and 5xx failures are not retried automatically, to avoid duplicate notes. Re-run the
ticket through the workflow or use `POST /retry/{ticket_id}` after resolving the cause.

Archive storage commits before terminal Zammad updates. If the PDF and sidecar exist but the ticket
is `pdf:error` or stuck in `pdf:processing`, first verify the pair is complete and the sidecar
checksum matches the PDF. Then inspect the logs and process-local history for a terminal tag
failure. Preserve the archive pair, clear a stale `pdf:processing` only after confirming no job is
still running, and use the reprocessing workflow below. There is no automatic reconciliation or
durable outbox in the supported single-process topology.

## Reprocessing workflow

1. Read the latest Chronikwerk ticket note and classification.
2. Fix the root cause: storage, credentials, network, signing, TSA, or payload.
3. Remove a stale `pdf:processing` if present.
4. Ensure the trigger tag is present, unless you are using `POST /retry/{ticket_id}`.
5. Remove `pdf:signed` only when you want new archive output.
6. Trigger a fresh Zammad update or call `POST /retry/{ticket_id}`.
7. Confirm `pdf:signed` or a new `pdf:error` note.

## Troubleshooting

### `403 forbidden` on `/ingest`

Check that `ZAMMAD__WEBHOOK_HMAC_SECRET` matches, that the `X-Hub-Signature` header is present, and
that no proxy transforms the request body after signing.

### `503 webhook_auth_not_configured`

Normal startup rejects a missing webhook secret. Set `ZAMMAD__WEBHOOK_HMAC_SECRET` to a random,
non-placeholder value of at least 32 characters.

### `400 missing_delivery_id`

`hardening.webhook.require_delivery_id=true` is enabled and the request lacks `X-Zammad-Delivery`.

### Ticket ends in `pdf:error`

Check:

- storage mount path, permissions, free space, and quota
- Zammad API token permissions
- signing PFX path/password
- TSA URL, credentials, and trust
- `pdf.max_articles` and `pdf.article_limit_mode` for large tickets; attachment binaries are not
  archived and have no byte-limit setting

### Ticket remains `pdf:processing`

The process may have exited during background work. Inspect the logs and `/jobs/history`. If an
archive pair already exists, treat it as a possible post-commit finalization failure and follow the
archive consistency guidance above before running the reprocessing workflow.

## On-call fast triage

1. Did `/ingest` return `202`?
2. What is the current ticket tag state?
3. What does the latest internal note say?
4. Is the expected destination path writable?
5. Could delivery-ID dedupe have skipped the replay?
6. Does `GET /healthz?deep=true` report writable storage?

## Release safety reminders

- Protect `/metrics` with a bearer token or network policy when enabled.
- Treat CIFS/SMB durability as a storage-system contract, not an app guarantee.
- Validate signing and timestamp trust in the target environment.
- Run the [release checklist](release-checklist.md) before publication.
