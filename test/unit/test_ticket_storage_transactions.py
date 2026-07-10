from __future__ import annotations

# Directly exercises the private marker writer and preserves existing import grouping.
# pylint: disable=protected-access,wrong-import-order
# ruff: noqa: I001  # Pylint and Ruff classify the in-repository test package differently.

import json
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest

from test.unit.test_ticket_storage_errors import _settings, _snapshot
from zammad_pdf_archiver.app.jobs import ticket_storage
from zammad_pdf_archiver.domain.snapshot_models import AttachmentMeta


def _paths(tmp_path: Path) -> tuple[Path, Path]:
    pdf = tmp_path / "archive" / "Ticket-10001.pdf"
    return pdf, pdf.with_suffix(".pdf.json")


def _store(tmp_path: Path, pdf_bytes: bytes) -> tuple[Path, Path]:
    settings = _settings(tmp_path)
    target, sidecar = _paths(tmp_path)
    ticket_storage.store_ticket_files(
        pdf_bytes=pdf_bytes,
        snapshot=_snapshot(),
        target_path=target,
        sidecar_path=sidecar,
        ticket_id=100,
        now=datetime(2025, 1, 1, tzinfo=UTC),
        settings=settings,
    )
    return target, sidecar


def _fail_new_sidecar_move(sidecar: Path, original_move: Any):
    def move(src: Path, dst: Path, *_args: Any, **kwargs: Any) -> None:
        if dst == sidecar and src.name.endswith(sidecar.name):
            raise OSError("sidecar move failed")
        original_move(src, dst, *_args, **kwargs)

    return move


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

    assert not target.exists()
    assert not sidecar.exists()
    assert not list(target.parent.glob(".tmp-archiving-*"))


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


def test_attachment_metadata_is_not_archived_as_binary_or_sidecar_entries(
    tmp_path: Path,
) -> None:
    snapshot = _snapshot()
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
    settings = _settings(tmp_path)
    target, sidecar = _paths(tmp_path)

    ticket_storage.store_ticket_files(
        pdf_bytes=b"%PDF",
        snapshot=snapshot,
        target_path=target,
        sidecar_path=sidecar,
        ticket_id=100,
        now=datetime(2025, 1, 1, tzinfo=UTC),
        settings=settings,
    )

    assert not (target.parent / "attachments").exists()
    assert "attachments" not in json.loads(sidecar.read_text(encoding="utf-8"))


class _SimulatedCrash(BaseException):
    pass


@pytest.mark.parametrize(
    ("phase", "expected_pdf"),
    [
        (ticket_storage.TransactionPhase.PREPARED, b"old-pdf"),
        (ticket_storage.TransactionPhase.PDF_BACKED_UP, b"old-pdf"),
        (ticket_storage.TransactionPhase.PRIOR_PAIR_BACKED_UP, b"old-pdf"),
        (ticket_storage.TransactionPhase.PDF_INSTALLED, b"new-pdf"),
        (ticket_storage.TransactionPhase.NEW_PAIR_INSTALLED, b"new-pdf"),
    ],
)
def test_startup_reconciles_every_durable_interruption_phase(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    phase: ticket_storage.TransactionPhase,
    expected_pdf: bytes,
) -> None:
    target, sidecar = _store(tmp_path, b"old-pdf")
    old_sidecar = sidecar.read_bytes()
    original_write_marker = ticket_storage._write_marker

    def crash_after_marker(
        transaction: ticket_storage.ArchiveTransaction,
        *,
        storage_root: Path,
        fsync: bool,
    ) -> None:
        original_write_marker(transaction, storage_root=storage_root, fsync=fsync)
        if transaction.phase is phase:
            raise _SimulatedCrash

    monkeypatch.setattr(ticket_storage, "_write_marker", crash_after_marker)
    with pytest.raises(_SimulatedCrash):
        _store(tmp_path, b"new-pdf")
    monkeypatch.setattr(ticket_storage, "_write_marker", original_write_marker)

    assert ticket_storage.recover_archive_transactions(tmp_path, fsync=False) == 1
    assert target.read_bytes() == expected_pdf
    if expected_pdf == b"old-pdf":
        assert sidecar.read_bytes() == old_sidecar
    else:
        sidecar_data = json.loads(sidecar.read_text(encoding="utf-8"))
        assert sidecar_data["sha256"] == sha256(expected_pdf).hexdigest()
    assert not list(tmp_path.rglob("*.bak.*"))
    assert not list(target.parent.glob(".tmp-archiving-*"))
    assert not list((tmp_path / ".archive-transactions").glob("*.json"))


@pytest.mark.parametrize(
    ("interruption", "expected_pdf"),
    [
        ("pdf_backup", b"old-pdf"),
        ("sidecar_backup", b"old-pdf"),
        ("pdf_install", b"old-pdf"),
        ("sidecar_install", b"new-pdf"),
    ],
)
def test_startup_reconciles_crash_between_move_and_next_phase_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    interruption: str,
    expected_pdf: bytes,
) -> None:
    target, sidecar = _store(tmp_path, b"old-pdf")
    old_sidecar = sidecar.read_bytes()
    original_move = ticket_storage.move_file_within_root

    def crash_after_move(src: Path, dst: Path, *_args: Any, **kwargs: Any) -> None:
        original_move(src, dst, *_args, **kwargs)
        is_temp = src.parent.name.startswith(".tmp-archiving-")
        reached = {
            "pdf_backup": dst.name.startswith(f"{target.name}.bak."),
            "sidecar_backup": dst.name.startswith(f"{sidecar.name}.bak."),
            "pdf_install": dst == target and is_temp,
            "sidecar_install": dst == sidecar and is_temp,
        }[interruption]
        if reached:
            raise _SimulatedCrash

    monkeypatch.setattr(ticket_storage, "move_file_within_root", crash_after_move)
    with pytest.raises(_SimulatedCrash):
        _store(tmp_path, b"new-pdf")
    monkeypatch.setattr(ticket_storage, "move_file_within_root", original_move)

    assert ticket_storage.recover_archive_transactions(tmp_path, fsync=False) == 1
    assert target.read_bytes() == expected_pdf
    if expected_pdf == b"old-pdf":
        assert sidecar.read_bytes() == old_sidecar
    else:
        assert json.loads(sidecar.read_text(encoding="utf-8"))["sha256"] == sha256(
            b"new-pdf"
        ).hexdigest()
    assert not list(tmp_path.rglob("*.bak.*"))
    assert not list(target.parent.glob(".tmp-archiving-*"))
    assert not list((tmp_path / ".archive-transactions").glob("*.json"))


def test_failed_rollback_is_retained_and_recovery_can_be_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target, sidecar = _store(tmp_path, b"old-pdf")
    original_move = ticket_storage.move_file_within_root

    def fail_sidecar_install_and_pdf_restore(
        src: Path, dst: Path, *_args: Any, **kwargs: Any
    ) -> None:
        if dst == sidecar and src.parent.name.startswith(".tmp-archiving-"):
            raise OSError("sidecar install failed")
        if dst == target and ".bak." in src.name:
            raise OSError("prior PDF restore failed")
        original_move(src, dst, *_args, **kwargs)

    monkeypatch.setattr(
        ticket_storage,
        "move_file_within_root",
        fail_sidecar_install_and_pdf_restore,
    )
    with pytest.raises(ticket_storage.ArchiveRecoveryError, match="transaction retained"):
        _store(tmp_path, b"new-pdf")

    markers = list((tmp_path / ".archive-transactions").glob("*.json"))
    assert len(markers) == 1
    with pytest.raises(ticket_storage.ArchiveRecoveryError):
        ticket_storage.recover_archive_transactions(tmp_path, fsync=False)
    assert markers[0].exists()

    monkeypatch.setattr(ticket_storage, "move_file_within_root", original_move)
    assert ticket_storage.recover_archive_transactions(tmp_path, fsync=False) == 1
    assert target.read_bytes() == b"old-pdf"
    assert json.loads(sidecar.read_text(encoding="utf-8"))["sha256"] == sha256(
        b"old-pdf"
    ).hexdigest()
    assert ticket_storage.recover_archive_transactions(tmp_path, fsync=False) == 0
    assert not list(tmp_path.rglob("*.bak.*"))


def test_recovery_rejects_malicious_marker_paths_without_touching_outside_file(
    tmp_path: Path,
) -> None:
    storage_root = tmp_path / "root"
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"do-not-touch")
    transaction = ticket_storage.ArchiveTransaction(
        transaction_id="a" * 32,
        ticket_id=100,
        phase=ticket_storage.TransactionPhase.PREPARED,
        target_relative="../outside.pdf",
        sidecar_relative="../outside.pdf.json",
        new_pdf_sha256=sha256(b"new").hexdigest(),
        new_sidecar_sha256=sha256(b"new-sidecar").hexdigest(),
        prior_pdf_sha256=None,
        prior_sidecar_sha256=None,
    )
    ticket_storage._write_marker(transaction, storage_root=storage_root, fsync=False)

    with pytest.raises(ticket_storage.ArchiveRecoveryError, match="unsafe path"):
        ticket_storage.recover_archive_transactions(storage_root, fsync=False)

    assert outside.read_bytes() == b"do-not-touch"
    assert list((storage_root / ".archive-transactions").glob("*.json"))
