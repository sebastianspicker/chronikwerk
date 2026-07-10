# Deployment

This project ships production-oriented Docker and Docker Compose artifacts. Use
one external environment file for both Compose interpolation and the container;
set `ARCHIVER_ENV_FILE` in that file to its installed path.

## Prerequisites

- Linux host with Docker Engine and Docker Compose v2.
- Mounted archive storage path, for example an SMB/CIFS mount.
- Zammad API token and webhook HMAC secret.
- Optional PKCS#12/PFX signing material when signing is enabled.

## Suggested Layout

- Repository and compose files: `/opt/zammad-ticket-archiver`
- Environment and secrets: `/etc/zammad-archiver`
- Archive mount: `/mnt/archive` or another dedicated path

```bash
sudo mkdir -p /opt/zammad-ticket-archiver
sudo mkdir -p /etc/zammad-archiver/secrets
sudo rsync -a --delete ./ /opt/zammad-ticket-archiver/
```

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
