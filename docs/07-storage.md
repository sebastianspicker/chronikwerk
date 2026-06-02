# 07 - Storage

This document describes how archive files are written and what storage assumptions are required.

## 1. Output Files

Each successful ticket run writes:
- one PDF file
- one sidecar JSON file (`<pdf_filename>.json`)
- optionally: attachment binaries in an `attachments/` subdir when `pdf.include_attachment_binary=true` (see [config-reference](config-reference.md)); the sidecar includes `attachment_summary` counts for tickets with attachments and, when binaries are written, an `attachments` array with `storage_path`, `article_id`, `attachment_id`, `filename`, and `sha256` per file.
- if `pdf.include_attachment_binary=true`, an in-budget attachment fetch failure
  fails the archive job before storage commit; the service must not write a
  successful archive while silently omitting that binary.
- attachments omitted because binary inclusion is disabled or configured size
  budgets are exhausted remain successful policy omissions, but the sidecar must
  report written and omitted counts plus omission reasons.

Output root is configured by:
- `storage.root` / `STORAGE_ROOT`

Layout:
- `<storage.root>/<archive_user>/<archive_path...>/<filename>.pdf`
- sidecar next to PDF: `<filename>.pdf.json`
- when attachment binary inclusion is enabled: `<same_dir>/attachments/<sanitized_name>` for each included attachment

Path building and validation:
- `src/zammad_pdf_archiver/adapters/storage/layout.py`
- `src/zammad_pdf_archiver/domain/path_policy.py`

## 2. Permissions Model

Default container user:
- UID/GID `10001:10001`

Required permissions on target filesystem:
- execute (`x`) on parent directories
- write (`w`) in destination directory
- create/remove temporary files and temporary work directories

For CIFS/SMB mounts, share ACLs and UID/GID mapping must permit these operations.

## 3. Commit Behavior

Archive commits always use the ticket-storage commit path:
1. create a temporary work directory under the target archive directory
2. write attachments, PDF, and sidecar into the temporary work directory
3. move files into their final locations with `os.replace`
4. publish the sidecar last so its presence is the completeness signal
5. optionally fsync files/directories when `storage.fsync=true`

`storage.atomic_write` / `STORAGE_ATOMIC_WRITE` is not a supported setting.

Implementation:
- `src/zammad_pdf_archiver/app/jobs/ticket_storage.py`
- `src/zammad_pdf_archiver/adapters/storage/fs_storage.py`

## 4. Path Safety and Symlink Defense

Before writing, storage layer enforces:
- final path must remain under `storage.root`
- no symlink traversal under storage root path components
- destination directory creation when missing

This reduces path traversal and symlink abuse risk.

**Residual risk (TOCTOU):** The symlink check runs before the write. A symlink could be created between the check and the write. For high-assurance deployments, use a dedicated mount or filesystem controls; see [09-security.md](09-security.md).

**O_NOFOLLOW:** File opens use `O_NOFOLLOW` where the platform supports it to avoid following a symlink at the final path component. On platforms without `O_NOFOLLOW`, see [09-security.md](09-security.md) (Residual risks).

## 5. CIFS/SMB Deployment Assumptions

Recommended production pattern:
1. mount share on host OS
2. bind-mount host path into container as `STORAGE_ROOT`

Why:
- keeps mount credentials/lifecycle outside container
- avoids privileged mount operations in app container

Helper script:
- `scripts/ops/mount-cifs.sh`

Treat helper script as baseline only; review options for your environment.

## 6. Operational Checklist

- confirm effective `STORAGE_ROOT` path
- confirm mount is read/write
- verify UID/GID mapping for runtime user
- verify ACLs on all parent directories
- verify quota/free space

If write failures continue, check ticket error note and service logs, then follow:
- [`08-operations.md`](08-operations.md)

## 7. Durability and Integrity Notes

The service writes checksums and optional signatures, but does not enforce immutable storage policy itself.

For archive-grade operation, use storage-platform controls:
- snapshots/versioning
- append-only or tamper-evident controls
- periodic checksum/signature verification
