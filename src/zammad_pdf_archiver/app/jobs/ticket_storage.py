"""Write PDFs, audit sidecars, and attachments to the archive filesystem.

All writes go through a temp directory first; files are renamed into place atomically.
The sidecar JSON is moved last so its presence reliably signals a complete archival.
"""

from __future__ import annotations

import json
import os
import shutil
import time as _time
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from zammad_pdf_archiver.adapters.storage.fs_storage import (
    ensure_dir,
    move_file_within_root,
    write_bytes,
)
from zammad_pdf_archiver.domain.audit import build_audit_record, compute_sha256
from zammad_pdf_archiver.domain.path_policy import sanitize_segment

if TYPE_CHECKING:
    from zammad_pdf_archiver.config.settings import Settings
    from zammad_pdf_archiver.domain.snapshot_models import Snapshot


@dataclass(frozen=True)
class StorageResult:
    target_path: Path
    sidecar_path: Path
    sha256_hex: str
    size_bytes: int


@dataclass(frozen=True)
class StoragePaths:
    target_dir: Path
    target_path: Path
    sidecar_path: Path


def compute_storage_paths(
    storage_root: Path,
    username: str,
    archive_path_segments: list[str],
    allow_prefixes: list[str] | None,
    filename_pattern: str,
    ticket_number: str,
    date_iso: str,
) -> StoragePaths:
    """Resolve the final target path, sidecar path, and parent directory for a ticket."""
    from zammad_pdf_archiver.adapters.storage.layout import (
        build_filename_from_pattern,
        build_target_dir,
    )

    target_dir = build_target_dir(
        storage_root,
        username,
        archive_path_segments,
        allow_prefixes=allow_prefixes,
    )

    filename = build_filename_from_pattern(
        filename_pattern,
        ticket_number=ticket_number,
        timestamp_utc=date_iso,
    )

    target_path = target_dir / filename
    sidecar_path = target_path.with_name(target_path.name + ".json")

    return StoragePaths(
        target_dir=target_dir,
        target_path=target_path,
        sidecar_path=sidecar_path,
    )


def _write_attachments(
    tmp_dir: Path,
    snapshot: Snapshot,
    storage_root: Path,
    attachments_dir: Path,
    *,
    fsync: bool,
) -> list[dict[str, Any]]:
    """Write attachment files to *tmp_dir* and return audit metadata entries.

    Only articles whose attachments carry binary content are written.
    Returns an empty list when there are no attachments to write.
    """
    snapshot_articles = snapshot.articles
    if not snapshot_articles:
        return []

    has_attachments = any(
        att.content is not None for article in snapshot_articles for att in article.attachments
    )
    if not has_attachments:
        return []

    temp_attachments_dir = tmp_dir / "attachments"
    ensure_dir(temp_attachments_dir)
    entries: list[dict[str, Any]] = []

    for article in snapshot_articles:
        for att in article.attachments:
            if att.content is None:
                continue
            safe_name = (
                sanitize_segment(f"{article.id}_{att.attachment_id or 0}_{att.filename or 'bin'}")
                or f"article_{article.id}_{att.attachment_id or 0}"
            )
            write_bytes(
                temp_attachments_dir / safe_name,
                att.content,
                fsync=fsync,
                storage_root=storage_root,
            )
            entries.append(
                {
                    "storage_path": str(attachments_dir / safe_name),
                    "article_id": article.id,
                    "attachment_id": att.attachment_id,
                    "filename": att.filename,
                    "sha256": compute_sha256(att.content),
                }
            )
    return entries


def _build_and_write_audit(
    tmp_dir: Path,
    sidecar_name: str,
    *,
    ticket_id: int,
    snapshot: Snapshot,
    now: datetime,
    target_path: Path,
    sha256_hex: str,
    settings: Settings,
    attachment_entries: list[dict[str, Any]],
) -> None:
    """Build the audit record and write the sidecar JSON into *tmp_dir*."""
    audit_record = build_audit_record(
        ticket_id=ticket_id,
        ticket_number=snapshot.ticket.number,
        title=snapshot.ticket.title,
        created_at=now,
        storage_path=str(target_path),
        sha256=sha256_hex,
        signing_settings=settings.signing,
        attachments=attachment_entries if attachment_entries else None,
    )
    audit_bytes = (
        json.dumps(audit_record, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")
    write_bytes(
        tmp_dir / sidecar_name,
        audit_bytes,
        fsync=settings.storage.fsync,
        storage_root=settings.storage.root,
    )


def _backup_if_exists(path: Path, *, storage_root: Path, fsync: bool) -> None:
    """If *path* already exists, rename it with a ``.bak.<timestamp>`` suffix."""
    if not path.exists():
        return
    timestamp = str(int(_time.time()))
    backup_path = path.with_name(f"{path.name}.bak.{timestamp}")
    move_file_within_root(path, backup_path, storage_root=storage_root, fsync=fsync)


def _commit_files_to_storage(
    tmp_dir: Path,
    paths: StoragePaths,
    attachment_entries: list[dict[str, Any]],
    *,
    storage_root: Path,
    fsync: bool,
) -> None:
    """Atomically rename files from *tmp_dir* into their final locations.

    Order matters: attachments first, then the PDF, then the sidecar last.
    The sidecar arriving last signals a complete, successful archival.
    """
    temp_attachments_dir = tmp_dir / "attachments"
    if attachment_entries:
        attachments_dir = paths.target_path.parent / "attachments"
        ensure_dir(attachments_dir)
        for entry in attachment_entries:
            fname = Path(entry["storage_path"]).name
            dst = attachments_dir / fname
            _backup_if_exists(dst, storage_root=storage_root, fsync=fsync)
            move_file_within_root(
                temp_attachments_dir / fname,
                dst,
                storage_root=storage_root,
                fsync=fsync,
            )

    _backup_if_exists(paths.target_path, storage_root=storage_root, fsync=fsync)
    move_file_within_root(
        tmp_dir / paths.target_path.name,
        paths.target_path,
        storage_root=storage_root,
        fsync=fsync,
    )

    # Sidecar last — its presence signals successful archival.
    # If sidecar move fails, remove the orphaned PDF to maintain atomicity.
    _backup_if_exists(paths.sidecar_path, storage_root=storage_root, fsync=fsync)
    try:
        move_file_within_root(
            tmp_dir / paths.sidecar_path.name,
            paths.sidecar_path,
            storage_root=storage_root,
            fsync=fsync,
        )
    except Exception:
        _log = structlog.get_logger(__name__)
        _log.error(
            "ticket_storage.sidecar_move_failed_removing_orphan_pdf",
            pdf_path=str(paths.target_path),
        )
        try:
            os.remove(paths.target_path)
        except OSError:
            _log.error(
                "ticket_storage.orphan_pdf_removal_failed",
                pdf_path=str(paths.target_path),
            )
        raise


def store_ticket_files(
    pdf_bytes: bytes,
    snapshot: Snapshot,
    paths: StoragePaths,
    ticket_id: int,
    now: datetime,
    settings: Settings,
) -> StorageResult:
    """Write PDF, audit sidecar, and any attachment binaries to their final paths.

    Uses a temp directory under the target parent; all files are renamed into place.
    The sidecar is moved last so its presence reliably indicates a complete archival.
    """
    sha256_hex = compute_sha256(pdf_bytes)
    size_bytes = len(pdf_bytes)

    temp_archive_root = (
        paths.target_path.parent / f".tmp-archiving-{ticket_id}-{uuid.uuid4().hex[:8]}"
    )

    try:
        ensure_dir(temp_archive_root)

        # 1. Write attachment binaries into temp dir.
        attachments_dir = paths.target_path.parent / "attachments"
        attachment_entries = _write_attachments(
            temp_archive_root,
            snapshot,
            settings.storage.root,
            attachments_dir,
            fsync=settings.storage.fsync,
        )

        # 2. Write PDF into temp dir.
        write_bytes(
            temp_archive_root / paths.target_path.name,
            pdf_bytes,
            fsync=settings.storage.fsync,
            storage_root=settings.storage.root,
        )

        # 3. Build audit record and write sidecar JSON into temp dir.
        _build_and_write_audit(
            temp_archive_root,
            paths.sidecar_path.name,
            ticket_id=ticket_id,
            snapshot=snapshot,
            now=now,
            target_path=paths.target_path,
            sha256_hex=sha256_hex,
            settings=settings,
            attachment_entries=attachment_entries,
        )

        # 4. Atomic commit: rename all files into their final locations.
        _commit_files_to_storage(
            temp_archive_root,
            paths,
            attachment_entries,
            storage_root=settings.storage.root,
            fsync=settings.storage.fsync,
        )
    finally:
        if temp_archive_root.exists():
            shutil.rmtree(temp_archive_root, ignore_errors=True)

    return StorageResult(
        target_path=paths.target_path,
        sidecar_path=paths.sidecar_path,
        sha256_hex=sha256_hex,
        size_bytes=size_bytes,
    )
