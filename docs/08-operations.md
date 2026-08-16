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
- `/admin`: optional session-authenticated overview, volatile history, retry, and staged
  non-secret configuration.

`202` means accepted, not archived. Confirm completion by checking final tags,
the internal ticket note, logs, and archive output.

When the bounded in-process admission limit is full, ingest returns `503` with
`code=job_capacity_exhausted` and `Retry-After: 1`; no background task was
accepted. Batch requests are rejected as a whole when their jobs do not fit.

## Start and Stop

```bash
sudo docker compose --env-file /etc/chronikwerk/chronikwerk.env up -d --build
sudo docker compose --env-file /etc/chronikwerk/chronikwerk.env ps
sudo docker compose --env-file /etc/chronikwerk/chronikwerk.env logs -f
sudo docker compose --env-file /etc/chronikwerk/chronikwerk.env down
```

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

## Update and Rollback

Update:

```bash
cd /opt/chronikwerk
sudo git fetch --tags --prune
sudo git checkout <new-release-tag>
sudo docker compose --env-file /etc/chronikwerk/chronikwerk.env up -d --build
```

Update from a clean tag or a versioned image. Never synchronize a developer working tree
into `/opt`: ignored local configuration, credentials, archives, evidence, and tool state
must remain outside the deployment source tree.

Rollback:

```bash
cd /opt/chronikwerk
sudo git checkout <known-good-commit-or-tag>
sudo docker compose --env-file /etc/chronikwerk/chronikwerk.env up -d --build
```

For no-build rollbacks, publish versioned images and pin compose `image:` tags.

Managed configuration remains restart-only. The UI labels a newly staged revision as
inactive until the process is restarted externally. If a staged revision prevents web
startup, use the offline CLI against the same external configuration and state directory:

```bash
chronikwerk-admin list-config-revisions
chronikwerk-admin stage-config-rollback <full-revision-hash>
sudo docker compose --env-file /etc/chronikwerk/chronikwerk.env restart
```

## Optional systemd Wrapper

```bash
sudo install -m 0644 infra/systemd/chronikwerk.service /etc/systemd/system/chronikwerk.service
sudo systemctl daemon-reload
sudo systemctl enable --now chronikwerk.service
```

The unit assumes `/opt/chronikwerk`. Adjust `WorkingDirectory=` if
your deployment path differs.

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
- optional Prometheus metrics

Treat logs and internal ticket notes as sensitive operational data. They can contain
delivery identifiers and absolute archive paths.

## Idempotency and Retry Limits

The default runtime is single-instance and process-local:

- Graceful shutdown gives admitted work `admission.shutdown_timeout_seconds`
  to drain before async cancellation. In-flight PDF, signing, and filesystem
  worker threads cannot be stopped safely, so the service waits for them after
  cancellation; total shutdown can exceed the configured grace period. A
  process crash or abrupt termination can lose accepted background work.
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

Archive storage commits before terminal Zammad updates. If the PDF and sidecar
exist but the ticket is `pdf:error` or remains `pdf:processing`, first verify
that the pair is complete and that the sidecar checksum matches the PDF. Then
inspect logs and process-local history for a terminal tag failure. Preserve the
archive pair, clear stale `pdf:processing` only after confirming no job is still
running, and use the reprocessing workflow below. There is no automatic
reconciliation or durable outbox in the supported single-process topology.

## Reprocessing Workflow

1. Read the latest Chronikwerk ticket note and classification.
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

Normal startup rejects a missing webhook secret. Set
`ZAMMAD__WEBHOOK_HMAC_SECRET` to a random, non-placeholder value containing at least
32 characters.

### `400 missing_delivery_id`

`hardening.webhook.require_delivery_id=true` is enabled and the request lacks
`X-Zammad-Delivery`.

### Ticket ends in `pdf:error`

Check:

- storage mount path, permissions, free space, and quota
- Zammad API token permissions
- signing PFX path/password
- TSA URL, credentials, and trust
- `pdf.max_articles` and `pdf.article_limit_mode` for large tickets; attachment
  binaries are not archived and have no byte-limit setting

### Ticket remains `pdf:processing`

The process may have exited during background work. Inspect logs and
`/jobs/history`. If an archive pair already exists, treat it as a possible
post-commit finalization failure and follow the archive consistency guidance
above before running the reprocessing workflow.

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
