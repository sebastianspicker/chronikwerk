# 07 - Storage

The storage adapter writes PDFs and audit sidecars under `storage.root`.

## Output Shape

For each successful archive:

```text
<storage.root>/<archive-user>/<archive-path>/Ticket-<number>_<timestamp>.pdf
<storage.root>/<archive-user>/<archive-path>/Ticket-<number>_<timestamp>.pdf.json
```

The exact filename comes from `storage.filename_pattern`.

## Safety Properties

- Path segments are validated and sanitized.
- Final paths are resolved under `storage.root`.
- Symlinks under the storage root are rejected before writes.
- PDF and sidecar writes use atomic replace behavior.
- Optional fsync is enabled by default with `storage.fsync=true`.

## Operational Checks

Before production use, verify:

- mount exists and is read-write
- service UID/GID can create directories and files
- enough free space and quota
- `GET /healthz?deep=true` reports writable storage
- one real archive run produces both PDF and sidecar

## CIFS/SMB Notes

CIFS/SMB durability and locking semantics depend on mount options, server
behavior, and network reliability. Treat the share as an operational dependency
and monitor write failures.
