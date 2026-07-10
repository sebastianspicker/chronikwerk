"""Write PDFs and audit sidecars to the archive filesystem.

All writes go through a temp directory first; files are renamed into place atomically.
The sidecar JSON is moved last so its presence reliably signals a complete archival.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

import structlog

from zammad_pdf_archiver.adapters.storage.fs_storage import (
    _fsync_dir_best_effort,
    _reject_symlinks_under_root,
    ensure_dir,
    move_file_within_root,
    remove_file_within_root,
    write_atomic_bytes,
    write_bytes,
)
from zammad_pdf_archiver.domain.audit import AuditRecordInput, build_audit_record
from zammad_pdf_archiver.domain.path_policy import ensure_within_root

if TYPE_CHECKING:
    from zammad_pdf_archiver.config.settings import Settings
    from zammad_pdf_archiver.domain.snapshot_models import Snapshot


@dataclass(frozen=True)
class StorageResult:
    """Implement the StorageResult operation."""
    target_path: Path
    sidecar_path: Path
    sha256_hex: str
    size_bytes: int


@dataclass(frozen=True)
class AuditWriteContext:
    """Implement the AuditWriteContext operation."""
    ticket_id: int
    snapshot: Snapshot
    now: datetime
    target_path: Path
    sha256_hex: str
    settings: Settings


class ArchiveRecoveryError(RuntimeError):
    """A durable archive transaction could not be reconciled safely."""


class TransactionPhase(StrEnum):
    """Durable phases of a PDF and sidecar replacement."""

    PREPARED = "prepared"
    PDF_BACKED_UP = "pdf_backed_up"
    PRIOR_PAIR_BACKED_UP = "prior_pair_backed_up"
    PDF_INSTALLED = "pdf_installed"
    NEW_PAIR_INSTALLED = "new_pair_installed"


@dataclass(frozen=True)
class ArchiveTransaction:  # pylint: disable=too-many-instance-attributes
    """Content of a path-confined transaction marker."""

    transaction_id: str
    ticket_id: int
    phase: TransactionPhase
    target_relative: str
    sidecar_relative: str
    new_pdf_sha256: str
    new_sidecar_sha256: str
    prior_pdf_sha256: str | None
    prior_sidecar_sha256: str | None


@dataclass(frozen=True)
class TransactionPaths:  # pylint: disable=too-many-instance-attributes
    """Validated paths derived from an archive transaction."""

    marker: Path
    target: Path
    sidecar: Path
    temp_dir: Path
    temp_pdf: Path
    temp_sidecar: Path
    pdf_backup: Path
    sidecar_backup: Path


_MARKER_DIRECTORY = ".archive-transactions"
_MARKER_VERSION = 1
_MARKER_KEYS = {
    "version",
    "transaction_id",
    "ticket_id",
    "phase",
    "target_relative",
    "sidecar_relative",
    "new_pdf_sha256",
    "new_sidecar_sha256",
    "prior_pdf_sha256",
    "prior_sidecar_sha256",
}


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


def _log_cleanup_failure(operation: str, path: Path, exc: BaseException) -> None:
    structlog.get_logger(__name__).error(
        "ticket_storage.cleanup_failed",
        operation=operation,
        path=str(path),
        error=str(exc),
    )


def _sha256_path(path: Path) -> str | None:
    if path.is_symlink():
        raise ArchiveRecoveryError(f"refusing to inspect symlink: {path}")
    if not path.exists():
        return None
    if not path.is_file():
        raise ArchiveRecoveryError(f"transaction path is not a regular file: {path}")
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_digest(value: object, *, field: str, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str) or len(value) != 64:
        raise ArchiveRecoveryError(f"invalid transaction marker field: {field}")
    try:
        int(value, 16)
    except ValueError as exc:
        raise ArchiveRecoveryError(f"invalid transaction marker field: {field}") from exc
    return value


def _transaction_payload(transaction: ArchiveTransaction) -> bytes:
    payload = {
        "version": _MARKER_VERSION,
        "transaction_id": transaction.transaction_id,
        "ticket_id": transaction.ticket_id,
        "phase": transaction.phase.value,
        "target_relative": transaction.target_relative,
        "sidecar_relative": transaction.sidecar_relative,
        "new_pdf_sha256": transaction.new_pdf_sha256,
        "new_sidecar_sha256": transaction.new_sidecar_sha256,
        "prior_pdf_sha256": transaction.prior_pdf_sha256,
        "prior_sidecar_sha256": transaction.prior_sidecar_sha256,
    }
    return (json.dumps(payload, sort_keys=True, indent=2) + "\n").encode()


def _load_transaction_payload(data: bytes) -> dict[str, object]:
    try:
        payload = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ArchiveRecoveryError("invalid archive transaction marker JSON") from exc
    if not isinstance(payload, dict) or set(payload) != _MARKER_KEYS:
        raise ArchiveRecoveryError("invalid archive transaction marker schema")
    if payload["version"] != _MARKER_VERSION:
        raise ArchiveRecoveryError("unsupported archive transaction marker version")
    return payload


def _validated_transaction_id(value: object) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 32
        or any(char not in "0123456789abcdef" for char in value)
    ):
        raise ArchiveRecoveryError("invalid archive transaction id")
    return value


def _validated_ticket_id(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ArchiveRecoveryError("invalid archive transaction ticket id")
    return value


def _validated_phase(value: object) -> TransactionPhase:
    if not isinstance(value, str):
        raise ArchiveRecoveryError("invalid archive transaction phase")
    try:
        return TransactionPhase(value)
    except (TypeError, ValueError) as exc:
        raise ArchiveRecoveryError("invalid archive transaction phase") from exc


def _validated_relative_paths(target: object, sidecar: object) -> tuple[str, str]:
    target_relative = target
    sidecar_relative = sidecar
    if not isinstance(target_relative, str) or not isinstance(sidecar_relative, str):
        raise ArchiveRecoveryError("invalid archive transaction path")
    return target_relative, sidecar_relative


def _parse_transaction(data: bytes) -> ArchiveTransaction:
    payload = _load_transaction_payload(data)
    target_relative, sidecar_relative = _validated_relative_paths(
        payload["target_relative"], payload["sidecar_relative"]
    )
    return ArchiveTransaction(
        transaction_id=_validated_transaction_id(payload["transaction_id"]),
        ticket_id=_validated_ticket_id(payload["ticket_id"]),
        phase=_validated_phase(payload["phase"]),
        target_relative=target_relative,
        sidecar_relative=sidecar_relative,
        new_pdf_sha256=_validate_digest(payload["new_pdf_sha256"], field="new_pdf_sha256")
        or "",
        new_sidecar_sha256=_validate_digest(
            payload["new_sidecar_sha256"], field="new_sidecar_sha256"
        )
        or "",
        prior_pdf_sha256=_validate_digest(
            payload["prior_pdf_sha256"], field="prior_pdf_sha256", optional=True
        ),
        prior_sidecar_sha256=_validate_digest(
            payload["prior_sidecar_sha256"], field="prior_sidecar_sha256", optional=True
        ),
    )


def _relative_to_root(path: Path, storage_root: Path) -> str:
    ensure_within_root(storage_root, path)
    _reject_symlinks_under_root(storage_root, path.parent)
    if path.is_symlink():
        raise ArchiveRecoveryError(f"refusing transaction path symlink: {path}")
    return path.resolve(strict=False).relative_to(storage_root.resolve(strict=False)).as_posix()


def _path_from_relative(storage_root: Path, value: str) -> Path:
    relative = PurePosixPath(value)
    if (
        not value
        or "\\" in value
        or relative.is_absolute()
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise ArchiveRecoveryError("transaction marker contains an unsafe path")
    path = storage_root.joinpath(*relative.parts)
    ensure_within_root(storage_root, path)
    _reject_symlinks_under_root(storage_root, path.parent)
    if path.is_symlink():
        raise ArchiveRecoveryError(f"transaction path is a symlink: {path}")
    return path


def _paths_for_transaction(
    transaction: ArchiveTransaction,
    *,
    storage_root: Path,
    marker_path: Path,
) -> TransactionPaths:
    marker_directory = storage_root / _MARKER_DIRECTORY
    expected_marker = marker_directory / f"{transaction.transaction_id}.json"
    if marker_path != expected_marker:
        raise ArchiveRecoveryError("transaction marker name does not match its id")
    target = _path_from_relative(storage_root, transaction.target_relative)
    sidecar = _path_from_relative(storage_root, transaction.sidecar_relative)
    if sidecar != target.with_name(f"{target.name}.json"):
        raise ArchiveRecoveryError("transaction sidecar path does not match the PDF path")
    relative_parts = PurePosixPath(transaction.target_relative).parts
    if (
        relative_parts[0] == _MARKER_DIRECTORY
        or any(part.startswith(".tmp-archiving-") for part in relative_parts)
        or ".bak." in target.name
    ):
        raise ArchiveRecoveryError("transaction marker targets a reserved storage path")
    temp_dir = target.parent / (
        f".tmp-archiving-{transaction.ticket_id}-{transaction.transaction_id}"
    )
    if temp_dir.is_symlink():
        raise ArchiveRecoveryError("transaction temp directory is a symlink")
    return TransactionPaths(
        marker=marker_path,
        target=target,
        sidecar=sidecar,
        temp_dir=temp_dir,
        temp_pdf=temp_dir / target.name,
        temp_sidecar=temp_dir / sidecar.name,
        pdf_backup=target.with_name(f"{target.name}.bak.{transaction.transaction_id}"),
        sidecar_backup=sidecar.with_name(f"{sidecar.name}.bak.{transaction.transaction_id}"),
    )


def _marker_path(storage_root: Path, transaction_id: str) -> Path:
    return storage_root / _MARKER_DIRECTORY / f"{transaction_id}.json"


def _write_marker(
    transaction: ArchiveTransaction,
    *,
    storage_root: Path,
    fsync: bool,
) -> None:
    write_atomic_bytes(
        _marker_path(storage_root, transaction.transaction_id),
        _transaction_payload(transaction),
        storage_root=storage_root,
        fsync=fsync,
    )


def _with_phase(
    transaction: ArchiveTransaction, phase: TransactionPhase
) -> ArchiveTransaction:
    return ArchiveTransaction(
        transaction_id=transaction.transaction_id,
        ticket_id=transaction.ticket_id,
        phase=phase,
        target_relative=transaction.target_relative,
        sidecar_relative=transaction.sidecar_relative,
        new_pdf_sha256=transaction.new_pdf_sha256,
        new_sidecar_sha256=transaction.new_sidecar_sha256,
        prior_pdf_sha256=transaction.prior_pdf_sha256,
        prior_sidecar_sha256=transaction.prior_sidecar_sha256,
    )


def _remove_file(
    path: Path,
    *,
    storage_root: Path,
    fsync: bool,
    missing_ok: bool = True,
) -> None:
    remove_file_within_root(
        path,
        storage_root=storage_root,
        fsync=fsync,
        missing_ok=missing_ok,
    )


def _remove_temp_directory(
    paths: TransactionPaths, *, storage_root: Path, fsync: bool
) -> None:
    if paths.temp_dir.is_symlink():
        raise ArchiveRecoveryError("transaction temp directory is a symlink")
    if not paths.temp_dir.exists():
        return
    expected = {paths.temp_pdf, paths.temp_sidecar}
    unexpected = set(paths.temp_dir.iterdir()) - expected
    if unexpected:
        raise ArchiveRecoveryError("transaction temp directory contains unexpected files")
    for candidate in expected:
        _remove_file(candidate, storage_root=storage_root, fsync=fsync)
    paths.temp_dir.rmdir()
    if fsync:
        _fsync_dir_best_effort(paths.temp_dir.parent)


def _cleanup_transaction(
    paths: TransactionPaths, *, storage_root: Path, fsync: bool
) -> None:
    _remove_file(paths.pdf_backup, storage_root=storage_root, fsync=fsync)
    _remove_file(paths.sidecar_backup, storage_root=storage_root, fsync=fsync)
    _remove_temp_directory(paths, storage_root=storage_root, fsync=fsync)
    _remove_file(
        paths.marker,
        storage_root=storage_root,
        fsync=fsync,
        missing_ok=False,
    )


def _matches(path: Path, expected: str | None) -> bool:
    return _sha256_path(path) == expected


def _can_restore_component(canonical: Path, backup: Path, expected: str | None) -> bool:
    if expected is None:
        return not backup.exists()
    return _matches(canonical, expected) or _matches(backup, expected)


def _restore_component(
    canonical: Path,
    backup: Path,
    expected: str | None,
    *,
    storage_root: Path,
    fsync: bool,
) -> None:
    if expected is None:
        _remove_file(canonical, storage_root=storage_root, fsync=fsync)
        return
    if _matches(canonical, expected):
        return
    if not _matches(backup, expected):
        raise ArchiveRecoveryError(f"prior archive file is unavailable: {canonical}")
    _remove_file(canonical, storage_root=storage_root, fsync=fsync)
    move_file_within_root(backup, canonical, storage_root=storage_root, fsync=fsync)


def _restore_prior_pair(
    transaction: ArchiveTransaction,
    paths: TransactionPaths,
    *,
    storage_root: Path,
    fsync: bool,
) -> None:
    if not _can_restore_component(
        paths.target, paths.pdf_backup, transaction.prior_pdf_sha256
    ) or not _can_restore_component(
        paths.sidecar, paths.sidecar_backup, transaction.prior_sidecar_sha256
    ):
        raise ArchiveRecoveryError("complete prior PDF and sidecar pair cannot be restored")
    _restore_component(
        paths.target,
        paths.pdf_backup,
        transaction.prior_pdf_sha256,
        storage_root=storage_root,
        fsync=fsync,
    )
    _restore_component(
        paths.sidecar,
        paths.sidecar_backup,
        transaction.prior_sidecar_sha256,
        storage_root=storage_root,
        fsync=fsync,
    )
    if not _matches(paths.target, transaction.prior_pdf_sha256) or not _matches(
        paths.sidecar, transaction.prior_sidecar_sha256
    ):
        raise ArchiveRecoveryError("restored prior archive pair failed checksum validation")


def _new_component_available(canonical: Path, temp: Path, expected: str) -> bool:
    return _matches(canonical, expected) or _matches(temp, expected)


def _install_new_component(
    canonical: Path,
    temp: Path,
    expected: str,
    *,
    storage_root: Path,
    fsync: bool,
) -> None:
    if _matches(canonical, expected):
        return
    if not _matches(temp, expected):
        raise ArchiveRecoveryError(f"new archive file is unavailable: {canonical}")
    _remove_file(canonical, storage_root=storage_root, fsync=fsync)
    move_file_within_root(temp, canonical, storage_root=storage_root, fsync=fsync)


def _finalize_new_pair(
    transaction: ArchiveTransaction,
    paths: TransactionPaths,
    *,
    storage_root: Path,
    fsync: bool,
) -> None:
    if not _new_component_available(
        paths.target, paths.temp_pdf, transaction.new_pdf_sha256
    ) or not _new_component_available(
        paths.sidecar, paths.temp_sidecar, transaction.new_sidecar_sha256
    ):
        raise ArchiveRecoveryError("complete checksum-valid new archive pair is unavailable")
    _install_new_component(
        paths.target,
        paths.temp_pdf,
        transaction.new_pdf_sha256,
        storage_root=storage_root,
        fsync=fsync,
    )
    _install_new_component(
        paths.sidecar,
        paths.temp_sidecar,
        transaction.new_sidecar_sha256,
        storage_root=storage_root,
        fsync=fsync,
    )
    if not _matches(paths.target, transaction.new_pdf_sha256) or not _matches(
        paths.sidecar, transaction.new_sidecar_sha256
    ):
        raise ArchiveRecoveryError("new archive pair failed checksum validation")


def _recover_transaction(
    transaction: ArchiveTransaction,
    paths: TransactionPaths,
    *,
    storage_root: Path,
    prefer_rollback: bool,
    fsync: bool,
) -> None:
    new_pair_is_canonical = _matches(
        paths.target, transaction.new_pdf_sha256
    ) and _matches(paths.sidecar, transaction.new_sidecar_sha256)
    prefer_new_pair = new_pair_is_canonical or (
        not prefer_rollback
        and transaction.phase
        in {TransactionPhase.PDF_INSTALLED, TransactionPhase.NEW_PAIR_INSTALLED}
    )
    can_finalize = _new_component_available(
        paths.target, paths.temp_pdf, transaction.new_pdf_sha256
    ) and _new_component_available(
        paths.sidecar, paths.temp_sidecar, transaction.new_sidecar_sha256
    )
    can_restore = _can_restore_component(
        paths.target, paths.pdf_backup, transaction.prior_pdf_sha256
    ) and _can_restore_component(
        paths.sidecar, paths.sidecar_backup, transaction.prior_sidecar_sha256
    )
    if prefer_new_pair and can_finalize:
        _finalize_new_pair(
            transaction, paths, storage_root=storage_root, fsync=fsync
        )
    elif can_restore:
        _restore_prior_pair(
            transaction, paths, storage_root=storage_root, fsync=fsync
        )
    elif can_finalize:
        _finalize_new_pair(
            transaction, paths, storage_root=storage_root, fsync=fsync
        )
    else:
        raise ArchiveRecoveryError(
            "neither the prior nor the new archive pair can be recovered"
        )
    _cleanup_transaction(paths, storage_root=storage_root, fsync=fsync)


def _read_transaction(marker_path: Path, *, storage_root: Path) -> ArchiveTransaction:
    ensure_within_root(storage_root, marker_path)
    _reject_symlinks_under_root(storage_root, marker_path.parent)
    if marker_path.is_symlink() or not marker_path.is_file():
        raise ArchiveRecoveryError(f"invalid archive transaction marker: {marker_path}")
    if marker_path.stat().st_size > 64 * 1024:
        raise ArchiveRecoveryError("archive transaction marker is too large")
    return _parse_transaction(marker_path.read_bytes())


def recover_archive_transactions(storage_root: Path, *, fsync: bool = True) -> int:
    """Reconcile all durable archive transactions before accepting traffic."""
    root = Path(storage_root)
    marker_directory = root / _MARKER_DIRECTORY
    ensure_within_root(root, marker_directory)
    if marker_directory.is_symlink():
        raise ArchiveRecoveryError("archive transaction directory is a symlink")
    if not marker_directory.exists():
        return 0
    if not marker_directory.is_dir():
        raise ArchiveRecoveryError("archive transaction path is not a directory")
    recovered = 0
    for marker_path in sorted(marker_directory.iterdir()):
        if marker_path.name.startswith(".") and ".tmp-" in marker_path.name:
            _remove_file(marker_path, storage_root=root, fsync=fsync)
            continue
        if marker_path.suffix != ".json":
            raise ArchiveRecoveryError(
                f"unexpected file in archive transaction directory: {marker_path.name}"
            )
        transaction = _read_transaction(marker_path, storage_root=root)
        paths = _paths_for_transaction(
            transaction,
            storage_root=root,
            marker_path=marker_path,
        )
        try:
            _recover_transaction(
                transaction,
                paths,
                storage_root=root,
                prefer_rollback=False,
                fsync=fsync,
            )
        except ArchiveRecoveryError:
            raise
        except Exception as exc:  # pylint: disable=broad-exception-caught
            raise ArchiveRecoveryError(
                f"archive transaction recovery failed: {transaction.transaction_id}"
            ) from exc
        recovered += 1
    return recovered


def _commit_files_to_storage(
    tmp_dir: Path,
    target_path: Path,
    sidecar_path: Path,
    *,
    transaction_id: str,
    ticket_id: int,
    storage_root: Path,
    fsync: bool,
) -> None:
    """Replace a PDF pair using a durable, crash-recoverable transaction marker."""
    prior_pdf_sha256 = _sha256_path(target_path)
    prior_sidecar_sha256 = _sha256_path(sidecar_path)
    if (prior_pdf_sha256 is None) != (prior_sidecar_sha256 is None):
        raise ArchiveRecoveryError("existing archive PDF and sidecar pair is incomplete")
    transaction = ArchiveTransaction(
        transaction_id=transaction_id,
        ticket_id=ticket_id,
        phase=TransactionPhase.PREPARED,
        target_relative=_relative_to_root(target_path, storage_root),
        sidecar_relative=_relative_to_root(sidecar_path, storage_root),
        new_pdf_sha256=_sha256_path(tmp_dir / target_path.name) or "",
        new_sidecar_sha256=_sha256_path(tmp_dir / sidecar_path.name) or "",
        prior_pdf_sha256=prior_pdf_sha256,
        prior_sidecar_sha256=prior_sidecar_sha256,
    )
    marker_path = _marker_path(storage_root, transaction_id)
    paths = _paths_for_transaction(
        transaction,
        storage_root=storage_root,
        marker_path=marker_path,
    )
    _write_marker(transaction, storage_root=storage_root, fsync=fsync)
    try:
        if prior_pdf_sha256 is not None:
            move_file_within_root(
                target_path, paths.pdf_backup, storage_root=storage_root, fsync=fsync
            )
        transaction = _with_phase(transaction, TransactionPhase.PDF_BACKED_UP)
        _write_marker(transaction, storage_root=storage_root, fsync=fsync)
        if prior_sidecar_sha256 is not None:
            move_file_within_root(
                sidecar_path,
                paths.sidecar_backup,
                storage_root=storage_root,
                fsync=fsync,
            )
        transaction = _with_phase(transaction, TransactionPhase.PRIOR_PAIR_BACKED_UP)
        _write_marker(transaction, storage_root=storage_root, fsync=fsync)
        move_file_within_root(
            tmp_dir / target_path.name,
            target_path,
            storage_root=storage_root,
            fsync=fsync,
        )
        transaction = _with_phase(transaction, TransactionPhase.PDF_INSTALLED)
        _write_marker(transaction, storage_root=storage_root, fsync=fsync)
        move_file_within_root(
            tmp_dir / sidecar_path.name,
            sidecar_path,
            storage_root=storage_root,
            fsync=fsync,
        )
        transaction = _with_phase(transaction, TransactionPhase.NEW_PAIR_INSTALLED)
        _write_marker(transaction, storage_root=storage_root, fsync=fsync)
    except Exception:
        try:
            _recover_transaction(
                transaction,
                paths,
                storage_root=storage_root,
                prefer_rollback=True,
                fsync=fsync,
            )
        except Exception as recovery_error:
            raise ArchiveRecoveryError(
                f"archive rollback failed; transaction retained: {transaction_id}"
            ) from recovery_error
        raise
    try:
        _recover_transaction(
            transaction,
            paths,
            storage_root=storage_root,
            prefer_rollback=False,
            fsync=fsync,
        )
    except Exception as exc:
        raise ArchiveRecoveryError(
            f"archive commit cleanup failed; transaction retained: {transaction_id}"
        ) from exc


def store_ticket_files(
    pdf_bytes: bytes,
    *,
    snapshot: Snapshot,
    target_path: Path,
    sidecar_path: Path,
    ticket_id: int,
    now: datetime,
    settings: Settings,
) -> StorageResult:
    """Write a PDF and audit sidecar to their final paths.

    Uses a temp directory under the target parent; all files are renamed into place.
    The sidecar is moved last so its presence reliably indicates a complete archival.
    """
    sha256_hex = sha256(pdf_bytes).hexdigest()
    size_bytes = len(pdf_bytes)

    transaction_id = uuid.uuid4().hex
    temp_archive_root = target_path.parent / f".tmp-archiving-{ticket_id}-{transaction_id}"

    marker_path = _marker_path(settings.storage.root, transaction_id)
    try:
        ensure_dir(temp_archive_root)
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
            ),
        )
        _commit_files_to_storage(
            temp_archive_root,
            target_path,
            sidecar_path,
            transaction_id=transaction_id,
            ticket_id=ticket_id,
            storage_root=settings.storage.root,
            fsync=settings.storage.fsync,
        )
    finally:
        if temp_archive_root.exists() and not marker_path.exists():
            try:
                for child in temp_archive_root.iterdir():
                    _remove_file(
                        child,
                        storage_root=settings.storage.root,
                        fsync=settings.storage.fsync,
                    )
                temp_archive_root.rmdir()
            except Exception as exc:  # pylint: disable=broad-exception-caught
                _log_cleanup_failure("temp_remove", temp_archive_root, exc)

    return StorageResult(
        target_path=target_path,
        sidecar_path=sidecar_path,
        sha256_hex=sha256_hex,
        size_bytes=size_bytes,
    )
