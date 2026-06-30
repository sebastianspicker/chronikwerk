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
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from zammad_pdf_archiver.adapters.storage.fs_storage import (
    ensure_dir,
    move_file_within_root,
    write_bytes,
)
from zammad_pdf_archiver.domain.audit import AuditRecordInput, build_audit_record
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
class AuditWriteContext:
    ticket_id: int
    snapshot: Snapshot
    now: datetime
    target_path: Path
    sha256_hex: str
    settings: Settings
    attachment_entries: list[dict[str, Any]]


def _iter_attachment_payloads(snapshot: Snapshot) -> list[tuple[Any, Any]]:
    return [
        (article, attachment)
        for article in snapshot.articles
        for attachment in article.attachments
        if getattr(attachment, "content", None) is not None
    ]


def _attachment_safe_name(article: Any, attachment: Any) -> str:
    return (
        sanitize_segment(
            f"{article.id}_{attachment.attachment_id or 0}_{attachment.filename or 'bin'}"
        )
        or f"article_{article.id}_{attachment.attachment_id or 0}"
    )


def _write_attachment_payload(
    *,
    temp_attachments_dir: Path,
    storage_root: Path,
    attachments_dir: Path,
    article: Any,
    attachment: Any,
    fsync: bool,
) -> dict[str, Any]:
    safe_name = _attachment_safe_name(article, attachment)
    content = getattr(attachment, "content", None)
    if content is None:
        raise ValueError("attachment content missing")
    write_bytes(
        temp_attachments_dir / safe_name,
        content,
        fsync=fsync,
        storage_root=storage_root,
    )
    return {
        "storage_path": str(attachments_dir / safe_name),
        "article_id": article.id,
        "attachment_id": attachment.attachment_id,
        "filename": attachment.filename,
        "sha256": sha256(attachment.content).hexdigest(),
    }


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
    attachment_payloads = _iter_attachment_payloads(snapshot)
    if not attachment_payloads:
        return []

    temp_attachments_dir = tmp_dir / "attachments"
    ensure_dir(temp_attachments_dir)
    return [
        _write_attachment_payload(
            temp_attachments_dir=temp_attachments_dir,
            storage_root=storage_root,
            attachments_dir=attachments_dir,
            article=article,
            attachment=attachment,
            fsync=fsync,
        )
        for article, attachment in attachment_payloads
    ]


def _build_and_write_audit(
    tmp_dir: Path,
    sidecar_name: str,
    *,
    context: AuditWriteContext,
) -> None:
    """Build the audit record and write the sidecar JSON into *tmp_dir*."""
    audit_record = build_audit_record(
        AuditRecordInput(
            ticket_id=context.ticket_id,
            ticket_number=context.snapshot.ticket.number,
            title=context.snapshot.ticket.title,
            created_at=context.now,
            storage_path=str(context.target_path),
            sha256=context.sha256_hex,
        ),
        signing_settings=context.settings.signing,
        attachments=context.attachment_entries if context.attachment_entries else None,
    )
    audit_bytes = (
        json.dumps(audit_record, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")
    write_bytes(
        tmp_dir / sidecar_name,
        audit_bytes,
        fsync=context.settings.storage.fsync,
        storage_root=context.settings.storage.root,
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
    _commit_attachments(
        tmp_dir,
        target_path,
        sidecar_path,
        attachment_entries,
        storage_root=storage_root,
        fsync=fsync,
    )
    _commit_pdf(
        tmp_dir, target_path, sidecar_path, storage_root=storage_root, fsync=fsync
    )
    _commit_sidecar(
        tmp_dir, target_path, sidecar_path, storage_root=storage_root, fsync=fsync
    )


def _commit_attachments(
    tmp_dir: Path,
    target_path: Path,
    _sidecar_path: Path,
    attachment_entries: list[dict[str, Any]],
    *,
    storage_root: Path,
    fsync: bool,
) -> None:
    temp_attachments_dir = tmp_dir / "attachments"
    if not attachment_entries:
        return

    attachments_dir = target_path.parent / "attachments"
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


def _commit_pdf(
    tmp_dir: Path,
    target_path: Path,
    _sidecar_path: Path,
    *,
    storage_root: Path,
    fsync: bool,
) -> None:
    _backup_if_exists(target_path, storage_root=storage_root, fsync=fsync)
    move_file_within_root(
        tmp_dir / target_path.name,
        target_path,
        storage_root=storage_root,
        fsync=fsync,
    )


def _commit_sidecar(
    tmp_dir: Path,
    target_path: Path,
    sidecar_path: Path,
    *,
    storage_root: Path,
    fsync: bool,
) -> None:
    _backup_if_exists(sidecar_path, storage_root=storage_root, fsync=fsync)
    try:
        move_file_within_root(
            tmp_dir / sidecar_path.name,
            sidecar_path,
            storage_root=storage_root,
            fsync=fsync,
        )
    except Exception:  # pylint: disable=broad-exception-caught
        _log = structlog.get_logger(__name__)
        _log.error(
            "ticket_storage.sidecar_move_failed_removing_orphan_pdf",
            pdf_path=str(target_path),
        )
        try:
            os.remove(target_path)
        except OSError:
            _log.error(
                "ticket_storage.orphan_pdf_removal_failed",
                pdf_path=str(target_path),
            )
        raise


def store_ticket_files(
    pdf_bytes: bytes,
    snapshot: Snapshot,
    target_path: Path,
    sidecar_path: Path,
    ticket_id: int,
    now: datetime,
    settings: Settings,
) -> StorageResult:
    """Write PDF, audit sidecar, and any attachment binaries to their final paths.

    Uses a temp directory under the target parent; all files are renamed into place.
    The sidecar is moved last so its presence reliably indicates a complete archival.
    """
    sha256_hex = sha256(pdf_bytes).hexdigest()
    size_bytes = len(pdf_bytes)

    temp_archive_root = (
        target_path.parent / f".tmp-archiving-{ticket_id}-{uuid.uuid4().hex[:8]}"
    )

    try:
        ensure_dir(temp_archive_root)
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
            temp_archive_root,
            sidecar_path.name,
            context=AuditWriteContext(
                ticket_id=ticket_id,
                snapshot=snapshot,
                now=now,
                target_path=target_path,
                sha256_hex=sha256_hex,
                settings=settings,
                attachment_entries=attachment_entries,
            ),
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
        if temp_archive_root.exists():
            shutil.rmtree(temp_archive_root, ignore_errors=True)

    return StorageResult(
        target_path=target_path,
        sidecar_path=sidecar_path,
        sha256_hex=sha256_hex,
        size_bytes=size_bytes,
    )
