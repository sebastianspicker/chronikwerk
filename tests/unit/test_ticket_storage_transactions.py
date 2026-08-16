"""Covers atomic ticket storage commits, rollback, and artifact recovery."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from chronikwerk.app.jobs import ticket_storage
from chronikwerk.domain.snapshot_models import AttachmentMeta
from tests.support.ticket_storage_helpers import (
    StorageRequestOverrides,
    assert_no_partial_artifacts,
    storage_request,
    storage_settings,
    storage_snapshot,
)


def _paths(tmp_path: Path) -> tuple[Path, Path]:
    """Derive the paired PDF and sidecar locations for one ticket."""
    pdf = tmp_path / "archive" / "Ticket-10001.pdf"
    return pdf, pdf.with_suffix(".pdf.json")


def _store(tmp_path: Path, pdf_bytes: bytes) -> tuple[Path, Path]:
    """Persist one artifact pair before simulating a replacement failure."""
    settings = storage_settings(tmp_path)
    target, sidecar = _paths(tmp_path)
    ticket_storage.store_ticket_files_request(
        storage_request(
            pdf_bytes=pdf_bytes,
            paths=(target, sidecar),
            settings=settings,
        )
    )
    return target, sidecar


def _fail_new_sidecar_move(sidecar: Path, original_move: Any):
    """Fail only the new-sidecar move to exercise transactional rollback."""

    def move(src: Path, dst: Path, *_args: Any, **kwargs: Any) -> None:
        if dst == sidecar and src.name.endswith(sidecar.name):
            raise OSError("sidecar move failed")
        original_move(src, dst, *_args, **kwargs)

    return move


def _assert_transaction_error(
    error: ticket_storage.StorageTransactionError,
    *,
    rollback_operation: str,
    rollback_message: str,
    recovery_paths: tuple[Path, ...],
) -> None:
    """Assert the common primary and rollback error contract."""
    assert isinstance(error.primary_error, OSError)
    assert str(error.primary_error) == "sidecar move failed"
    assert [(failure.operation, str(failure.error)) for failure in error.rollback_failures] == [
        (rollback_operation, rollback_message)
    ]
    assert error.recovery_paths == recovery_paths


def test_replacement_sidecar_failure_restores_complete_prior_pair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target, sidecar = _store(tmp_path, b"old-pdf")
    old_sidecar = sidecar.read_bytes()
    original_move = ticket_storage.move_file_within_root
    monkeypatch.setattr(
        ticket_storage,
        "move_file_within_root",
        _fail_new_sidecar_move(sidecar, original_move),
    )

    with pytest.raises(OSError, match="sidecar move failed"):
        _store(tmp_path, b"new-pdf")

    assert target.read_bytes() == b"old-pdf"
    assert sidecar.read_bytes() == old_sidecar
    assert not list(tmp_path.rglob("*.bak.*"))
    assert not list(target.parent.glob(".tmp-archiving-*"))


def test_first_write_sidecar_failure_leaves_no_partial_canonical_pair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target, sidecar = _paths(tmp_path)
    original_move = ticket_storage.move_file_within_root
    monkeypatch.setattr(
        ticket_storage,
        "move_file_within_root",
        _fail_new_sidecar_move(sidecar, original_move),
    )

    with pytest.raises(OSError, match="sidecar move failed"):
        _store(tmp_path, b"new-pdf")

    assert_no_partial_artifacts(target, sidecar)


def test_backup_failure_preserves_prior_pair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target, sidecar = _store(tmp_path, b"old-pdf")
    old_sidecar = sidecar.read_bytes()
    original_move = ticket_storage.move_file_within_root

    def fail_pdf_backup(src: Path, dst: Path, *_args: Any, **kwargs: Any) -> None:
        if dst.name.startswith(f"{target.name}.bak."):
            raise OSError("pdf backup failed")
        original_move(src, dst, *_args, **kwargs)

    monkeypatch.setattr(ticket_storage, "move_file_within_root", fail_pdf_backup)

    with pytest.raises(OSError, match="pdf backup failed"):
        _store(tmp_path, b"new-pdf")

    assert target.read_bytes() == b"old-pdf"
    assert sidecar.read_bytes() == old_sidecar
    assert not list(tmp_path.rglob("*.bak.*"))


def test_rollback_restore_failure_exposes_primary_error_and_backup_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target, sidecar = _store(tmp_path, b"old-pdf")
    original_move = ticket_storage.move_file_within_root

    def fail_sidecar_commit_and_restore(src: Path, dst: Path, *_args: Any, **kwargs: Any) -> None:
        if dst == sidecar and src.parent.name.startswith(".tmp-archiving-"):
            raise OSError("sidecar move failed")
        if dst == sidecar and src.name.startswith(f"{sidecar.name}.bak."):
            raise OSError("sidecar restore failed")
        original_move(src, dst, *_args, **kwargs)

    monkeypatch.setattr(ticket_storage, "move_file_within_root", fail_sidecar_commit_and_restore)

    with pytest.raises(ticket_storage.StorageTransactionError) as raised:
        _store(tmp_path, b"new-pdf")

    error = raised.value
    backups = list(tmp_path.rglob(f"{sidecar.name}.bak.*"))
    _assert_transaction_error(
        error,
        rollback_operation="rollback_restore",
        rollback_message="sidecar restore failed",
        recovery_paths=tuple(backups),
    )
    assert target.read_bytes() == b"old-pdf"
    assert not sidecar.exists()


def test_rollback_remove_failure_is_reported_with_primary_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _store(tmp_path, b"old-pdf")
    target, sidecar = _paths(tmp_path)
    original_move = ticket_storage.move_file_within_root
    original_unlink = ticket_storage.unlink_file_within_root

    def fail_sidecar_commit(src: Path, dst: Path, *_args: Any, **kwargs: Any) -> None:
        if dst == sidecar and src.parent.name.startswith(".tmp-archiving-"):
            raise OSError("sidecar move failed")
        original_move(src, dst, *_args, **kwargs)

    def fail_pdf_rollback_remove(path: Path, *args: Any, **kwargs: Any) -> None:
        if path == target:
            raise OSError("pdf rollback remove failed")
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(ticket_storage, "move_file_within_root", fail_sidecar_commit)
    monkeypatch.setattr(ticket_storage, "unlink_file_within_root", fail_pdf_rollback_remove)

    with pytest.raises(ticket_storage.StorageTransactionError) as raised:
        _store(tmp_path, b"new-pdf")

    _assert_transaction_error(
        raised.value,
        rollback_operation="rollback_remove",
        rollback_message="pdf rollback remove failed",
        recovery_paths=(),
    )


def test_attachment_metadata_is_not_archived_as_binary_or_sidecar_entries(
    tmp_path: Path,
) -> None:
    snapshot = storage_snapshot()
    snapshot = snapshot.model_copy(
        update={
            "articles": [
                snapshot.articles[0].model_copy(
                    update={
                        "attachments": [
                            AttachmentMeta(
                                article_id=10,
                                attachment_id=1,
                                filename="report.pdf",
                                size=4,
                            )
                        ]
                    }
                )
            ]
        }
    )
    settings = storage_settings(tmp_path)
    target, sidecar = _paths(tmp_path)

    ticket_storage.store_ticket_files_request(
        storage_request(
            pdf_bytes=b"%PDF",
            paths=(target, sidecar),
            settings=settings,
            overrides=StorageRequestOverrides(snapshot=snapshot),
        )
    )

    assert not (target.parent / "attachments").exists()
    assert "attachments" not in json.loads(sidecar.read_text(encoding="utf-8"))
