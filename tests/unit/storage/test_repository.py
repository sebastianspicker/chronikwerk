"""Verify transactional PDF and sidecar replacement failure boundaries."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

import pytest

from chronikwerk.documents.models import Snapshot, TicketMeta
from chronikwerk.storage import repository
from chronikwerk.storage.options import ArchiveStorageOptions, SigningProvenance
from chronikwerk.storage.repository import (
    StorageTransactionError,
    StoreTicketFilesRequest,
    store_ticket_files_request,
)

_OLD_PDF = b"old-pdf"
_OLD_SIDECAR = b'{"sha256":"old"}\n'
_NEW_PDF = b"new-pdf"


class _BoundaryInterruption(BaseException):
    """Simulate process interruption immediately after one atomic move."""


def _request(
    root: Path,
    target: Path,
    sidecar: Path,
    *,
    fsync: bool = False,
) -> StoreTicketFilesRequest:
    """Build a synthetic archive write request for transaction fault injection."""
    return StoreTicketFilesRequest(
        pdf_bytes=_NEW_PDF,
        snapshot=Snapshot(
            ticket=TicketMeta(id=42, number="42", title="Transactional replacement"),
            articles=[],
        ),
        target_path=target,
        sidecar_path=sidecar,
        ticket_id=42,
        now=datetime(2026, 9, 6, tzinfo=UTC),
        storage=ArchiveStorageOptions(
            root=root,
            fsync=fsync,
            filename_pattern="Ticket-{ticket}.pdf",
        ),
        signing_provenance=SigningProvenance(enabled=False, tsa_used=False),
    )


def _old_pair(root: Path) -> tuple[Path, Path]:
    """Publish a prior pair whose bytes identify rollback restoration."""
    target = root / "archive" / "Ticket-42.pdf"
    sidecar = target.with_suffix(".pdf.json")
    target.parent.mkdir(parents=True)
    target.write_bytes(_OLD_PDF)
    sidecar.write_bytes(_OLD_SIDECAR)
    return target, sidecar


def _backup_paths(target: Path, sidecar: Path) -> tuple[list[Path], list[Path]]:
    """Locate retained recovery files for both members of the pair."""
    return (
        list(target.parent.glob(f"{target.name}.bak.*")),
        list(sidecar.parent.glob(f"{sidecar.name}.bak.*")),
    )


def _assert_old_pair_restored(target: Path, sidecar: Path) -> None:
    """Check the canonical pair and complete transaction cleanup."""
    assert target.read_bytes() == _OLD_PDF
    assert sidecar.read_bytes() == _OLD_SIDECAR
    assert _backup_paths(target, sidecar) == ([], [])
    assert not list(target.parent.glob(".tmp-archiving-*"))


@pytest.mark.parametrize("failure_call", [1, 2, 3, 4])
@pytest.mark.parametrize("fail_after_move", [False, True])
def test_replacement_failure_restores_old_pair_at_every_commit_move(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_call: int,
    fail_after_move: bool,
) -> None:
    target, sidecar = _old_pair(tmp_path)
    real_move = repository.move_file_within_root
    calls = 0
    injected = OSError(f"move {failure_call} failed")

    def failing_move(
        src: Path,
        dst: Path,
        *,
        storage_root: Path,
        fsync: bool = True,
    ) -> None:
        nonlocal calls
        calls += 1
        if calls == failure_call and not fail_after_move:
            raise injected
        real_move(src, dst, storage_root=storage_root, fsync=fsync)
        if calls == failure_call and fail_after_move:
            raise injected

    monkeypatch.setattr(repository, "move_file_within_root", failing_move)

    with pytest.raises(OSError) as exc_info:
        store_ticket_files_request(_request(tmp_path, target, sidecar))

    assert exc_info.value is injected
    _assert_old_pair_restored(target, sidecar)


@pytest.mark.parametrize(
    ("interruption_call", "expected_pdf", "sidecar_exists", "pdf_backups", "sidecar_backups"),
    [
        (1, _OLD_PDF, False, 0, 1),
        (2, None, False, 1, 1),
        (3, _NEW_PDF, False, 1, 1),
        (4, _NEW_PDF, True, 1, 1),
    ],
)
def test_interruption_boundaries_never_expose_a_sidecar_without_a_pdf(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    interruption_call: int,
    expected_pdf: bytes | None,
    sidecar_exists: bool,
    pdf_backups: int,
    sidecar_backups: int,
) -> None:
    target, sidecar = _old_pair(tmp_path)
    real_move = repository.move_file_within_root
    calls = 0

    def interrupting_move(
        src: Path,
        dst: Path,
        *,
        storage_root: Path,
        fsync: bool = True,
    ) -> None:
        nonlocal calls
        calls += 1
        real_move(src, dst, storage_root=storage_root, fsync=fsync)
        if calls == interruption_call:
            raise _BoundaryInterruption

    monkeypatch.setattr(repository, "move_file_within_root", interrupting_move)

    with pytest.raises(_BoundaryInterruption):
        store_ticket_files_request(_request(tmp_path, target, sidecar))

    assert (target.read_bytes() if target.exists() else None) == expected_pdf
    assert sidecar.exists() is sidecar_exists
    assert not sidecar.exists() or target.exists()
    target_backups, sidecar_backup_paths = _backup_paths(target, sidecar)
    assert len(target_backups) == pdf_backups
    assert len(sidecar_backup_paths) == sidecar_backups
    assert not list(target.parent.glob(".tmp-archiving-*"))


def _install_rollback_failure(
    monkeypatch: pytest.MonkeyPatch,
    *,
    restore_call: int,
) -> tuple[OSError, list[tuple[Path, Path]]]:
    """Fail publication and one selected restoration move, retaining operation order."""
    real_move = repository.move_file_within_root
    primary_error = OSError("sidecar publication failed")
    moves: list[tuple[Path, Path]] = []

    def failing_move(
        src: Path,
        dst: Path,
        *,
        storage_root: Path,
        fsync: bool = True,
    ) -> None:
        moves.append((src, dst))
        call = len(moves)
        if call == 4:
            raise primary_error
        if call == restore_call:
            raise OSError(f"rollback move {restore_call} failed")
        real_move(src, dst, storage_root=storage_root, fsync=fsync)

    monkeypatch.setattr(repository, "move_file_within_root", failing_move)
    return primary_error, moves


def test_pdf_restore_failure_withholds_sidecar_and_preserves_both_backups(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target, sidecar = _old_pair(tmp_path)
    primary_error, moves = _install_rollback_failure(monkeypatch, restore_call=5)

    with pytest.raises(StorageTransactionError) as exc_info:
        store_ticket_files_request(_request(tmp_path, target, sidecar))

    error = exc_info.value
    assert error.primary_error is primary_error
    assert [(failure.operation, failure.path) for failure in error.rollback_failures] == [
        ("rollback_restore", moves[4][0])
    ]
    assert not target.exists()
    assert not sidecar.exists()
    target_backups, sidecar_backups = _backup_paths(target, sidecar)
    assert set(error.recovery_paths) == set(target_backups + sidecar_backups)
    assert len(moves) == 5


def test_sidecar_restore_failure_keeps_restored_pdf_and_sidecar_backup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target, sidecar = _old_pair(tmp_path)
    primary_error, moves = _install_rollback_failure(monkeypatch, restore_call=6)

    with pytest.raises(StorageTransactionError) as exc_info:
        store_ticket_files_request(_request(tmp_path, target, sidecar))

    error = exc_info.value
    target_backups, sidecar_backups = _backup_paths(target, sidecar)
    assert error.primary_error is primary_error
    assert [(failure.operation, failure.path) for failure in error.rollback_failures] == [
        ("rollback_restore", moves[5][0])
    ]
    assert target.read_bytes() == _OLD_PDF
    assert not sidecar.exists()
    assert target_backups == []
    assert error.recovery_paths == tuple(sidecar_backups)


def test_first_write_sidecar_failure_removes_partial_pdf(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "archive" / "Ticket-42.pdf"
    sidecar = target.with_suffix(".pdf.json")
    real_move = repository.move_file_within_root
    calls = 0

    def failing_move(
        src: Path,
        dst: Path,
        *,
        storage_root: Path,
        fsync: bool = True,
    ) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("sidecar publication failed")
        real_move(src, dst, storage_root=storage_root, fsync=fsync)

    monkeypatch.setattr(repository, "move_file_within_root", failing_move)

    with pytest.raises(OSError, match="sidecar publication failed"):
        store_ticket_files_request(_request(tmp_path, target, sidecar))

    assert not target.exists()
    assert not sidecar.exists()
    assert _backup_paths(target, sidecar) == ([], [])


def test_successful_replacement_orders_moves_and_preserves_fsync_and_permissions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target, sidecar = _old_pair(tmp_path)
    real_move = repository.move_file_within_root
    moves: list[tuple[Path, Path, Path, bool]] = []

    def recording_move(
        src: Path,
        dst: Path,
        *,
        storage_root: Path,
        fsync: bool = True,
    ) -> None:
        moves.append((src, dst, storage_root, fsync))
        real_move(src, dst, storage_root=storage_root, fsync=fsync)

    monkeypatch.setattr(repository, "move_file_within_root", recording_move)

    store_ticket_files_request(_request(tmp_path, target, sidecar, fsync=True))

    assert [src.name for src, _, _, _ in moves] == [
        sidecar.name,
        target.name,
        target.name,
        sidecar.name,
    ]
    assert moves[0][1].name.startswith(f"{sidecar.name}.bak.")
    assert moves[1][1].name.startswith(f"{target.name}.bak.")
    assert [dst for _, dst, _, _ in moves[2:]] == [target, sidecar]
    assert all(storage_root == tmp_path and fsync for _, _, storage_root, fsync in moves)
    assert target.read_bytes() == _NEW_PDF
    assert target.stat().st_mode & 0o777 == 0o640
    assert sidecar.stat().st_mode & 0o777 == 0o640
    assert _backup_paths(target, sidecar) == ([], [])


@pytest.mark.parametrize("replacement", [False, True])
def test_rollback_keeps_published_pdf_when_marker_removal_fails(
    tmp_path, monkeypatch, replacement
) -> None:
    target, sidecar = _old_pair(tmp_path)
    if not replacement:
        target.unlink()
        sidecar.unlink()
    real_move = repository.move_file_within_root
    real_unlink = repository.unlink_file_within_root

    def fail_after_sidecar_publication(src, dst, **kwargs):
        real_move(src, dst, **kwargs)
        if dst == sidecar and ".tmp-archiving-" in str(src):
            raise OSError("publication flush failed")

    def fail_marker_removal(path, **kwargs):
        if path == sidecar:
            raise OSError("cannot withdraw marker")
        return real_unlink(path, **kwargs)

    monkeypatch.setattr(repository, "move_file_within_root", fail_after_sidecar_publication)
    monkeypatch.setattr(repository, "unlink_file_within_root", fail_marker_removal)
    with pytest.raises(StorageTransactionError) as error:
        store_ticket_files_request(_request(tmp_path, target, sidecar))
    assert target.read_bytes() == _NEW_PDF
    assert json.loads(sidecar.read_text())["sha256"] == sha256(_NEW_PDF).hexdigest()
    assert len(error.value.recovery_paths) == (2 if replacement else 0)


def test_rollback_withholds_preexisting_orphan_sidecar(tmp_path, monkeypatch) -> None:
    target, sidecar = _old_pair(tmp_path)
    target.unlink()
    real_move = repository.move_file_within_root

    def fail_pdf_publication(src, dst, **kwargs):
        if dst == target:
            raise OSError("PDF publication failed")
        return real_move(src, dst, **kwargs)

    monkeypatch.setattr(repository, "move_file_within_root", fail_pdf_publication)
    with pytest.raises(StorageTransactionError) as error:
        store_ticket_files_request(_request(tmp_path, target, sidecar))
    assert not target.exists()
    assert not sidecar.exists()
    assert len(error.value.recovery_paths) == 1
    assert error.value.recovery_paths[0].read_bytes() == _OLD_SIDECAR
