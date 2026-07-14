# Deployment

This project ships production-oriented Docker and Docker Compose artifacts. Use
one external environment file for both Compose interpolation and the container;
set `ARCHIVER_ENV_FILE` in that file to its installed path.

## Prerequisites

- Linux host with Docker Engine and Docker Compose v2.
- Mounted archive storage path, for example an SMB/CIFS mount.
- Zammad API token and webhook HMAC secret.
- Optional PKCS#12/PFX signing material when signing is enabled.
- A persistent admin-state directory and TLS proxy before the admin feature is enabled.

## Suggested Layout

- Repository and compose files: `/opt/zammad-ticket-archiver`
- Environment and secrets: `/etc/zammad-archiver`
- Archive mount: `/mnt/archive` or another dedicated path
- Admin revision state: `/var/lib/zammad-pdf-archiver/admin`

```bash
sudo mkdir -p /etc/zammad-archiver/secrets
sudo git clone --branch <release-tag> --depth 1 \
  https://github.com/sebastianspicker/zammad-ticket-archiver.git \
  /opt/zammad-ticket-archiver
```

Deploy a clean tagged checkout or a published image. Do not copy a developer working
tree: it may contain ignored credentials, local configuration, archives, reports, or tool
state that do not belong on the deployment host.

## Configure Environment

Copy a template and edit it on the target host:

```bash
sudo install -m 0640 -o root -g root infra/systemd/zammad-archiver.env /etc/zammad-archiver/zammad-archiver.env
sudo ${EDITOR:-vi} /etc/zammad-archiver/zammad-archiver.env
```

Minimum values:

- `ZAMMAD__BASE_URL`
- `ZAMMAD__API_TOKEN`
- `ZAMMAD__WEBHOOK_HMAC_SECRET`
- `STORAGE__ROOT`

Keep this line in the installed file and update it if the location changes:

```bash
ARCHIVER_ENV_FILE=/etc/zammad-archiver/zammad-archiver.env
```

## Optional Signing Material

Store the real PFX outside the repository:

```bash
sudo install -m 0640 -o root -g root /path/to/signing.pfx /etc/zammad-archiver/secrets/signing.pfx
```

Then configure:

```bash
SIGNING__ENABLED=true
SIGNING__PFX_PATH=/run/secrets/signing.pfx
SIGNING__PFX_PASSWORD=CHANGE-ME
```

Mount the file into the container with a compose override:

```yaml
services:
  zammad-pdf-archiver:
    volumes:
      - /etc/zammad-archiver/secrets/signing.pfx:/run/secrets/signing.pfx:ro
```

## Start

```bash
cd /opt/zammad-ticket-archiver
sudo docker compose --env-file /etc/zammad-archiver/zammad-archiver.env up -d --build
curl -fsS "http://127.0.0.1:${SERVER__PORT:-8080}/healthz"
```

## Optional administration application

Keep `ADMIN__ENABLED=false` until the documented web and PDF release gates pass. Before
enabling it, provision the Compose `admin-state` volume (or an equivalent writable
systemd path), place the service behind TLS and the existing trusted-network boundary,
and set a high-entropy token of at least 32 characters outside Git. The
placeholder below must be replaced:

```bash
ADMIN__ENABLED=true
ADMIN__ACCESS_TOKEN=CHANGE-ME-AT-LEAST-32-CHARACTERS
ADMIN__STATE_DIR=/var/lib/zammad-pdf-archiver/admin
ADMIN__COOKIE_SECURE=true
```

The UI can stage allowlisted non-secret values but cannot restart the service. After a
stage operation, restart externally and verify that Overview shows the revision as
active. Offline recovery commands are:

```bash
zammad-archiver-cli list-config-revisions
zammad-archiver-cli stage-config-rollback <full-revision-hash>
```

## CIFS/SMB Storage

Mount the share on the host and point `STORAGE__ROOT` at the mountpoint.

Example one-off mount:

```bash
sudo mount -t cifs //fileserver/archive /mnt/archive \
  -o credentials=/etc/zammad-archiver/cifs.creds,uid=10001,gid=10001,iocharset=utf8,file_mode=0640,dir_mode=0750,noserverino
```

For production, prefer `/etc/fstab` or a managed mount unit with credentials
stored outside the repository.

## Operations

Start/stop, rollback, health checks, troubleshooting, and on-call steps live in
[08 - Operations](08-operations.md).
