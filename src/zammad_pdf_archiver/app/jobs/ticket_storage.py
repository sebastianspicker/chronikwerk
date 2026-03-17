"""Write PDFs, audit sidecars, and attachments to the archive filesystem.

All writes go through a temp directory first; files are renamed into place atomically.
The sidecar JSON is moved last so its presence reliably signals a complete archival.
"""
from __future__ import annotations

import json
import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

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
    attachment_entries: list[dict[str, Any]] = []
    
    try:
        ensure_dir(temp_archive_root)
        temp_pdf_path = temp_archive_root / paths.target_path.name
        temp_sidecar_path = temp_archive_root / paths.sidecar_path.name
        temp_attachments_dir = temp_archive_root / "attachments"
        
        attachments_dir = paths.target_path.parent / "attachments"
        snapshot_articles = snapshot.articles

        if snapshot_articles:
            has_attachments = any(
                att.content is not None
                for article in snapshot_articles
                for att in article.attachments
            )
            if has_attachments:
                ensure_dir(temp_attachments_dir)
                for article in snapshot_articles:
                    for att in article.attachments:
                        if att.content is None:
                            continue
                        safe_name = sanitize_segment(
                            f"{article.id}_{att.attachment_id or 0}_{att.filename or 'bin'}"
                        ) or f"article_{article.id}_{att.attachment_id or 0}"
                        attach_temp_path = temp_attachments_dir / safe_name
                        write_bytes(
                            attach_temp_path,
                            att.content,
                            fsync=settings.storage.fsync,
                            storage_root=settings.storage.root,
                        )
                        attachment_entries.append(
                            {
                                "storage_path": str(attachments_dir / safe_name),
                                "article_id": article.id,
                                "attachment_id": att.attachment_id,
                                "filename": att.filename,
                                "sha256": compute_sha256(att.content),
                            }
                        )
        
        audit_record = build_audit_record(
            ticket_id=ticket_id,
            ticket_number=snapshot.ticket.number,
            title=snapshot.ticket.title,
            created_at=now,
            storage_path=str(paths.target_path),
            sha256=sha256_hex,
            signing_settings=settings.signing,
            attachments=attachment_entries if attachment_entries else None,
        )
        audit_bytes = (
            json.dumps(audit_record, ensure_ascii=False, sort_keys=True, indent=2)
            + "\n"
        ).encode("utf-8")
        
        write_bytes(
            temp_pdf_path,
            pdf_bytes,
            fsync=settings.storage.fsync,
            storage_root=settings.storage.root,
        )
        write_bytes(
            temp_sidecar_path,
            audit_bytes,
            fsync=settings.storage.fsync,
            storage_root=settings.storage.root,
        )
        
        # ATOMIC "COMMIT" (Moves)
        # We use move_file_within_root which performs rename (atomic on same FS).
        if attachment_entries:
            ensure_dir(attachments_dir)
            for entry in attachment_entries:
                fname = Path(entry["storage_path"]).name
                move_file_within_root(
                    temp_attachments_dir / fname,
                    attachments_dir / fname,
                    storage_root=settings.storage.root,
                    fsync=settings.storage.fsync,
                )
        
        move_file_within_root(
            temp_pdf_path,
            paths.target_path,
            storage_root=settings.storage.root,
            fsync=settings.storage.fsync,
        )
        
        # Move Sidecar (Last: signals successful archival)
        move_file_within_root(
            temp_sidecar_path,
            paths.sidecar_path,
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
