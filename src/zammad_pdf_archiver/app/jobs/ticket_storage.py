"""Write PDFs, audit sidecars, and attachments to the archive filesystem.

All writes go through a temp directory first; files are renamed into place atomically.
The sidecar JSON is moved last so its presence reliably signals a complete archival.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import time as _time
from collections.abc import Iterator
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
from zammad_pdf_archiver.domain.audit import AuditRecordInput, build_audit_record, compute_sha256
from zammad_pdf_archiver.domain.path_policy import sanitize_segment

if TYPE_CHECKING:
    from zammad_pdf_archiver.config.settings import Settings
    from zammad_pdf_archiver.domain.snapshot_models import Article, AttachmentMeta, Snapshot

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class StorageResult:
    target_path: Path
    sidecar_path: Path
    sha256_hex: str
    size_bytes: int


@dataclass(frozen=True)
class _AuditSidecarRequest:
    tmp_dir: Path
    sidecar_name: str
    ticket_id: int
    snapshot: Snapshot
    now: datetime
    target_path: Path
    sha256_hex: str
    settings: Settings
    attachment_entries: list[dict[str, Any]]


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
    binary_attachments = list(_iter_binary_attachments(snapshot))
    if not binary_attachments:
        return []

    temp_attachments_dir = tmp_dir / "attachments"
    ensure_dir(temp_attachments_dir)
    entries: list[dict[str, Any]] = []

    for article, att, content in binary_attachments:
        safe_name = _attachment_safe_name(article, att)
        write_bytes(
            temp_attachments_dir / safe_name,
            content,
            fsync=fsync,
            storage_root=storage_root,
        )
        entries.append(_attachment_audit_entry(article, att, content, attachments_dir / safe_name))
    return entries


def _iter_binary_attachments(snapshot: Snapshot) -> Iterator[tuple[Article, AttachmentMeta, bytes]]:
    for article in snapshot.articles:
        for att in article.attachments:
            if att.content is not None:
                yield article, att, att.content


def _attachment_safe_name(article: Article, att: AttachmentMeta) -> str:
    fallback_name = f"article_{article.id}_{att.attachment_id or 0}"
    raw_name = f"{article.id}_{att.attachment_id or 0}_{att.filename or 'bin'}"
    return sanitize_segment(raw_name) or fallback_name


def _attachment_audit_entry(
    article: Article,
    att: AttachmentMeta,
    content: bytes,
    storage_path: Path,
) -> dict[str, Any]:
    return {
        "storage_path": str(storage_path),
        "article_id": article.id,
        "attachment_id": att.attachment_id,
        "filename": att.filename,
        "sha256": compute_sha256(content),
    }


def _attachment_summary(
    snapshot: Snapshot,
    attachment_entries: list[dict[str, Any]],
) -> dict[str, Any] | None:
    total = 0
    metadata_only = 0
    skipped = 0
    skipped_reasons: dict[str, int] = {}

    for article in snapshot.articles:
        for att in article.attachments:
            total += 1
            if att.content is None:
                metadata_only += 1
            if att.content_omission_reason:
                skipped += 1
                skipped_reasons[att.content_omission_reason] = (
                    skipped_reasons.get(att.content_omission_reason, 0) + 1
                )

    if total == 0:
        return None

    return {
        "total": total,
        "written": len(attachment_entries),
        "metadata_only": metadata_only,
        "skipped": skipped,
        "skipped_reasons": skipped_reasons,
    }


def _build_and_write_audit(request: _AuditSidecarRequest) -> None:
    """Build the audit record and write the sidecar JSON into *tmp_dir*."""
    attachment_summary = _attachment_summary(request.snapshot, request.attachment_entries)
    audit_record = build_audit_record(
        AuditRecordInput(
            ticket_id=request.ticket_id,
            ticket_number=request.snapshot.ticket.number,
            title=request.snapshot.ticket.title,
            created_at=request.now,
            storage_path=str(request.target_path),
            sha256=request.sha256_hex,
            signing_settings=request.settings.signing,
            attachments=request.attachment_entries if request.attachment_entries else None,
            attachment_summary=attachment_summary,
        )
    )
    audit_bytes = (
        json.dumps(audit_record, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")
    write_bytes(
        request.tmp_dir / request.sidecar_name,
        audit_bytes,
        fsync=request.settings.storage.fsync,
        storage_root=request.settings.storage.root,
    )


def _backup_if_exists(path: Path, *, storage_root: Path, fsync: bool) -> Path | None:
    """If *path* already exists, rename it with a ``.bak.<timestamp>`` suffix."""
    if not path.exists():
        return None
    timestamp = str(int(_time.time()))
    backup_path = path.with_name(f"{path.name}.bak.{timestamp}")
    move_file_within_root(path, backup_path, storage_root=storage_root, fsync=fsync)
    return backup_path


def _remove_committed_path(path: Path, *, event: str) -> None:
    _log = structlog.get_logger(__name__)
    try:
        os.remove(path)
    except FileNotFoundError:
        return
    except OSError:
        _log.error(event, path=str(path))


def _restore_backup(
    backup_path: Path | None,
    target_path: Path,
    *,
    storage_root: Path,
    fsync: bool,
    event: str,
) -> None:
    if backup_path is None:
        return
    _log = structlog.get_logger(__name__)
    try:
        move_file_within_root(
            backup_path,
            target_path,
            storage_root=storage_root,
            fsync=fsync,
        )
    except Exception:
        _log.error(event, backup_path=str(backup_path), target_path=str(target_path))


def _commit_files_to_storage(
    tmp_dir: Path,
    target_path: Path,
    sidecar_path: Path,
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
    moved_attachments = _commit_attachment_files(
        temp_attachments_dir,
        target_path.parent,
        attachment_entries,
        storage_root=storage_root,
        fsync=fsync,
    )

    pdf_backup = _backup_if_exists(target_path, storage_root=storage_root, fsync=fsync)
    move_file_within_root(
        tmp_dir / target_path.name,
        target_path,
        storage_root=storage_root,
        fsync=fsync,
    )

    # Sidecar last — its presence signals successful archival.
    # If sidecar move fails, roll back visible PDF/attachment side effects.
    sidecar_backup = _backup_if_exists(sidecar_path, storage_root=storage_root, fsync=fsync)
    try:
        _commit_sidecar_file(tmp_dir, sidecar_path, storage_root=storage_root, fsync=fsync)
    except Exception:
        _rollback_failed_sidecar_commit(
            target_path=target_path,
            sidecar_path=sidecar_path,
            moved_attachments=moved_attachments,
            pdf_backup=pdf_backup,
            sidecar_backup=sidecar_backup,
            storage_root=storage_root,
            fsync=fsync,
        )
        raise


def _commit_attachment_files(
    temp_attachments_dir: Path,
    target_parent: Path,
    attachment_entries: list[dict[str, Any]],
    *,
    storage_root: Path,
    fsync: bool,
) -> list[tuple[Path, Path | None]]:
    moved_attachments: list[tuple[Path, Path | None]] = []
    if not attachment_entries:
        return moved_attachments

    attachments_dir = target_parent / "attachments"
    ensure_dir(attachments_dir)
    for entry in attachment_entries:
        fname = Path(entry["storage_path"]).name
        dst = attachments_dir / fname
        attachment_backup = _backup_if_exists(dst, storage_root=storage_root, fsync=fsync)
        move_file_within_root(
            temp_attachments_dir / fname,
            dst,
            storage_root=storage_root,
            fsync=fsync,
        )
        moved_attachments.append((dst, attachment_backup))
    return moved_attachments


def _commit_sidecar_file(
    tmp_dir: Path,
    sidecar_path: Path,
    *,
    storage_root: Path,
    fsync: bool,
) -> None:
    move_file_within_root(
        tmp_dir / sidecar_path.name,
        sidecar_path,
        storage_root=storage_root,
        fsync=fsync,
    )


def _rollback_failed_sidecar_commit(
    *,
    target_path: Path,
    sidecar_path: Path,
    moved_attachments: list[tuple[Path, Path | None]],
    pdf_backup: Path | None,
    sidecar_backup: Path | None,
    storage_root: Path,
    fsync: bool,
) -> None:
    _log = structlog.get_logger(__name__)
    _log.error(
        "ticket_storage.sidecar_move_failed_removing_orphan_pdf",
        pdf_path=str(target_path),
    )
    _remove_committed_path(sidecar_path, event="ticket_storage.sidecar_cleanup_failed")
    _remove_committed_path(target_path, event="ticket_storage.orphan_pdf_removal_failed")
    _rollback_moved_attachments(
        moved_attachments,
        storage_root=storage_root,
        fsync=fsync,
    )
    _restore_backup(
        pdf_backup,
        target_path,
        storage_root=storage_root,
        fsync=fsync,
        event="ticket_storage.pdf_backup_restore_failed",
    )
    _restore_backup(
        sidecar_backup,
        sidecar_path,
        storage_root=storage_root,
        fsync=fsync,
        event="ticket_storage.sidecar_backup_restore_failed",
    )


def _rollback_moved_attachments(
    moved_attachments: list[tuple[Path, Path | None]],
    *,
    storage_root: Path,
    fsync: bool,
) -> None:
    for attachment_path, attachment_backup in reversed(moved_attachments):
        _remove_committed_path(
            attachment_path,
            event="ticket_storage.orphan_attachment_removal_failed",
        )
        _restore_backup(
            attachment_backup,
            attachment_path,
            storage_root=storage_root,
            fsync=fsync,
            event="ticket_storage.attachment_backup_restore_failed",
        )


def store_ticket_files(
    pdf_bytes: bytes,
    snapshot: Snapshot,
    target_path: Path,
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
    sidecar_path = target_path.with_name(target_path.name + ".json")

    ensure_dir(target_path.parent)
    temp_archive_root = Path(
        tempfile.mkdtemp(prefix=f".tmp-archiving-{ticket_id}-", dir=target_path.parent)
    )

    try:
        attachment_entries = _stage_ticket_files(
            temp_archive_root=temp_archive_root,
            pdf_bytes=pdf_bytes,
            snapshot=snapshot,
            target_path=target_path,
            ticket_id=ticket_id,
            now=now,
            sha256_hex=sha256_hex,
            settings=settings,
        )

        _commit_files_to_storage(
            temp_archive_root,
            target_path,
            sidecar_path,
            attachment_entries,
            storage_root=settings.storage.root,
            fsync=settings.storage.fsync,
        )
    finally:
        _cleanup_staging_dir(temp_archive_root)

    return StorageResult(
        target_path=target_path,
        sidecar_path=sidecar_path,
        sha256_hex=sha256_hex,
        size_bytes=size_bytes,
    )


def _stage_ticket_files(
    *,
    temp_archive_root: Path,
    pdf_bytes: bytes,
    snapshot: Snapshot,
    target_path: Path,
    ticket_id: int,
    now: datetime,
    sha256_hex: str,
    settings: Settings,
) -> list[dict[str, Any]]:
    attachments_dir = target_path.parent / "attachments"
    attachment_entries = _write_attachments(
        temp_archive_root,
        snapshot,
        settings.storage.root,
        attachments_dir,
        fsync=settings.storage.fsync,
    )
    write_bytes(
        temp_archive_root / target_path.name,
        pdf_bytes,
        fsync=settings.storage.fsync,
        storage_root=settings.storage.root,
    )
    _build_and_write_audit(
        _AuditSidecarRequest(
            tmp_dir=temp_archive_root,
            sidecar_name=target_path.name + ".json",
            ticket_id=ticket_id,
            snapshot=snapshot,
            now=now,
            target_path=target_path,
            sha256_hex=sha256_hex,
            settings=settings,
            attachment_entries=attachment_entries,
        )
    )
    return attachment_entries


def _cleanup_staging_dir(temp_archive_root: Path) -> None:
    if not temp_archive_root.exists():
        return
    try:
        shutil.rmtree(temp_archive_root)
    except OSError:
        log.warning(
            "staging_dir_cleanup_failed",
            path=str(temp_archive_root),
            exc_info=True,
        )
