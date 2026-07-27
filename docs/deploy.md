# Deployment

This project ships Docker and Docker Compose files for a single-host deployment. Use
one external environment file for both Compose interpolation and the container;
set `CHRONIKWERK_ENV_FILE` in that file to its installed path.

## Prerequisites

- Linux host with Docker Engine and Docker Compose 2.24.0 or newer.
- Mounted archive storage path, for example an SMB/CIFS mount.
- Zammad API token and webhook HMAC secret.
- Optional PKCS#12/PFX signing material when signing is enabled.
- A persistent admin-state directory and TLS proxy before the admin feature is enabled.

## Suggested Layout

- Repository and compose files: `/opt/chronikwerk`
- Environment and secrets: `/etc/chronikwerk`
- Archive mount: `/mnt/archive` or another dedicated path
- Admin revision state: `/var/lib/chronikwerk/admin`

```bash
sudo install -d -m 0755 /opt/chronikwerk
sudo install -d -m 0750 /etc/chronikwerk/secrets
```

Extract a reviewed source release into `/opt/chronikwerk`, or use a reviewed image when one
is available. Do not copy a developer working tree: it may contain ignored credentials,
local configuration, archives, reports, or tool state that do not belong on the deployment
host.

## Configure Environment

Copy a template and edit it on the target host:

```bash
cd /opt/chronikwerk
sudo install -m 0640 -o root -g root infra/systemd/chronikwerk.env.example /etc/chronikwerk/chronikwerk.env
sudo ${EDITOR:-vi} /etc/chronikwerk/chronikwerk.env
```

Minimum values:

- `ZAMMAD__BASE_URL`
- `ZAMMAD__API_TOKEN`
- `ZAMMAD__WEBHOOK_HMAC_SECRET`
- `STORAGE__ROOT`

The portable aliases `ZAMMAD_ORIGIN` and `ZAMMAD_API_TOKEN` may replace the
first two nested keys. Use one form only, or keep duplicate normalized values
identical; conflicting duplicates fail startup.

Keep this line in the installed file and update it if the location changes:

```bash
CHRONIKWERK_ENV_FILE=/etc/chronikwerk/chronikwerk.env
```

## Optional Signing Material

Store the real PFX outside the repository:

```bash
sudo install -m 0640 -o root -g root /path/to/signing.pfx /etc/chronikwerk/secrets/signing.pfx
```

Then configure:

```bash
SIGNING__ENABLED=true
SIGNING__PFX_PATH=/run/secrets/signing.pfx
SIGNING__PFX_PASSWORD=CHANGE-ME
```

Save this local-only override as `/opt/chronikwerk/docker-compose.override.yml`. Docker
Compose loads it with the checked-in `docker-compose.yml` when the commands below run from
`/opt/chronikwerk`.

```yaml
services:
  chronikwerk:
    volumes:
      - /etc/chronikwerk/secrets/signing.pfx:/run/secrets/signing.pfx:ro
```

Do not commit the host-specific override or signing file.

## Start

```bash
cd /opt/chronikwerk
sudo docker compose --env-file /etc/chronikwerk/chronikwerk.env up -d --build
sudo docker compose --env-file /etc/chronikwerk/chronikwerk.env exec -T chronikwerk \
  python -c 'import os,urllib.request; p=os.getenv("SERVER__PORT","8080"); print(urllib.request.urlopen(f"http://127.0.0.1:{p}/healthz", timeout=2).read().decode())'
```

Production Compose publishes the service on `127.0.0.1` by default so plaintext
HTTP is reachable only by a same-host TLS proxy. Set `CHRONIKWERK_PUBLISH_HOST` to a
different host address only when an approved ingress and firewall provide the
equivalent boundary; setting it to `0.0.0.0` exposes the plaintext application
port on every host interface.

## Optional administration application

Keep `ADMIN__ENABLED=false` until the documented web and PDF release gates pass. Before
enabling it, provision the Compose `admin-state` volume (or an equivalent writable
systemd path), place the service behind TLS and the existing trusted-network boundary,
and set a high-entropy token of at least 32 characters outside Git. The
placeholder below must be replaced:

```bash
ADMIN__ENABLED=true
ADMIN__ACCESS_TOKEN=CHANGE-ME-AT-LEAST-32-CHARACTERS
ADMIN__STATE_DIR=/var/lib/chronikwerk/admin
ADMIN__COOKIE_SECURE=true
```

The UI can stage allowlisted non-secret values but cannot restart the service. After a
stage operation, restart externally and verify that Overview shows the revision as
active. Offline recovery commands are:

```bash
chronikwerk-admin list-config-revisions
chronikwerk-admin stage-config-rollback <full-revision-hash>
```

## CIFS/SMB Storage

Mount the share on the host and point `STORAGE__ROOT` at the mountpoint.

Example one-off mount:

```bash
sudo mount -t cifs //fileserver/archive /mnt/archive \
  -o credentials=/etc/chronikwerk/cifs.creds,uid=10001,gid=10001,iocharset=utf8,file_mode=0640,dir_mode=0750,noserverino
```

For production, prefer `/etc/fstab` or a managed mount unit with credentials
stored outside the repository.

## Operations

Start/stop, rollback, health checks, troubleshooting, and on-call steps live in
[08 - Operations](08-operations.md).
