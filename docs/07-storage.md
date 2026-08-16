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
- Attachment binaries are not archived; attachment metadata remains in the PDF
  snapshot and templates only.
- Replacements use a collision-proof transaction backup. The sidecar is moved
  last as the completion marker; a failed commit restores the prior PDF and
  sidecar pair, or removes the partial PDF on a first write.
- Optional fsync is enabled by default with `storage.fsync=true`.

## Operational Checks

Before production use, verify:

- mount exists and is read-write
- service UID/GID can create directories and files
- enough free space and quota
- `GET /healthz?deep=true` reports writable storage
- one real archive run produces both PDF and sidecar

If a replacement fails during commit, the original canonical PDF and sidecar
remain the authoritative pair. Backup and rollback cleanup failures are logged
separately from the original write failure and may require filesystem cleanup.

The archive commit completes before Chronikwerk applies terminal Zammad tags or
creates a success note. A PDF and sidecar can therefore exist while the ticket is
`pdf:error`, still has `pdf:processing`, or otherwise reflects a partial tag
update. In that case, verify the PDF checksum against the sidecar and inspect the
processing history and logs before reprocessing. Do not delete a valid archive
solely because Zammad finalization failed; a retry may replace the canonical pair
according to the configured filename pattern.

## CIFS/SMB Notes

CIFS/SMB durability and locking semantics depend on mount options, server
behavior, and network reliability. Treat the share as an operational dependency
and monitor write failures.
