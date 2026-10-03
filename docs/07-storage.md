# Storage

The storage adapter writes PDFs and audit sidecars under `storage.root`.

## Output shape

For each successful archive:

```text
<storage.root>/<archive-user>/<archive-path>/Ticket-<number>_<timestamp>.pdf
<storage.root>/<archive-user>/<archive-path>/Ticket-<number>_<timestamp>.pdf.json
```

The exact filename comes from `storage.filename_pattern`.

## Safety properties

- Path segments are validated and sanitized.
- Final paths are resolved under `storage.root`.
- Symlinks under the storage root are rejected before writes.
- PDF and sidecar writes use atomic replace behavior.
- Attachment binaries are not archived; attachment metadata remains in the PDF
  snapshot and templates only.
- During a replacement, the old sidecar is withdrawn before the PDF is backed
  up. Collision-proof backups keep the previous pair while the new PDF and, after
  it, the completion sidecar are published. Rollback restores the PDF before its
  sidecar; if the PDF cannot be restored, the completion marker is withheld and
  the recovery backups are retained. A failed first write removes partial output.
- Optional fsync is enabled by default with `storage.fsync=true`.

## Operational checks

Before production use, verify:

- mount exists and is read-write
- service UID/GID can create directories and files
- enough free space and quota
- `GET /healthz?deep=true` reports writable storage
- one real archive run produces both PDF and sidecar

If a replacement fails during commit and rollback succeeds, the restored PDF and
sidecar remain authoritative. If rollback fails, retained transaction backups and
reported recovery paths require operator inspection. Verify the PDF checksum before
restoring a completion sidecar. Backup and rollback cleanup failures are reported
separately from the original write failure.

The archive commit completes before Chronikwerk applies terminal Zammad tags or
creates a success note. A PDF and sidecar can therefore exist while the ticket is
`pdf:error`, still has `pdf:processing`, or otherwise reflects a partial tag
update. In that case, verify the PDF checksum against the sidecar and inspect the
processing history and logs before reprocessing. Do not delete a valid archive
solely because Zammad finalization failed; a retry may replace the canonical pair
according to the configured filename pattern.

## CIFS/SMB notes

CIFS/SMB durability and locking semantics depend on mount options, server
behavior, and network reliability. Treat the share as an operational dependency
and monitor write failures.
