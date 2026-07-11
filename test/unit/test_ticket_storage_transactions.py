from __future__ import annotations

import json
from datetime import UTC, datetime
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
