from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from zammad_pdf_archiver.app.jobs import ticket_storage
from zammad_pdf_archiver.config.settings import Settings
from zammad_pdf_archiver.domain.snapshot_models import (
    Article,
    Snapshot,
    TicketMeta,
)


def _settings(tmp_path: Path) -> Settings:
    return Settings.from_mapping(
        {
            "zammad": {
                "base_url": "https://zammad.example.invalid",
                "api_token": "token",
            },
            "storage": {"root": str(tmp_path), "fsync": False},
        }
    )


def _snapshot(*, with_attachment: bool) -> Snapshot:
    attachments = []
    if with_attachment:
        attachments.append(
            SimpleNamespace(
                article_id=10,
                attachment_id=1,
                filename="report.pdf",
                size=4,
                content=b"data",
            )
        )
    return Snapshot(
        ticket=TicketMeta(
            id=100,
            number="10001",
            title="Storage failure",
            created_at=datetime(2025, 1, 1, tzinfo=UTC),
        ),
        articles=[
            Article.model_construct(
                id=10,
                created_at=datetime(2025, 1, 1, tzinfo=UTC),
                attachments=attachments,
            )
        ],
    )


def test_store_ticket_files_propagates_attachment_write_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    target = tmp_path / "archive" / "Ticket-10001.pdf"
    sidecar = target.with_suffix(".pdf.json")
    original_write_bytes = ticket_storage.write_bytes

    def fail_attachment_write(path: Path, *_args: Any, **_kwargs: Any) -> None:
        if "attachments" in path.parts:
            raise PermissionError("attachment write failed")
        original_write_bytes(path, *_args, **_kwargs)

    monkeypatch.setattr(ticket_storage, "write_bytes", fail_attachment_write)

    with pytest.raises(PermissionError, match="attachment write failed"):
        ticket_storage.store_ticket_files(
            pdf_bytes=b"%PDF",
            snapshot=_snapshot(with_attachment=True),
            target_path=target,
            sidecar_path=sidecar,
            ticket_id=100,
            now=datetime(2025, 1, 1, tzinfo=UTC),
            settings=settings,
        )

    assert not target.exists()
    assert not sidecar.exists()
    assert not list(target.parent.glob(".tmp-archiving-*"))


def test_store_ticket_files_sidecar_failure_removes_moved_pdf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    target = tmp_path / "archive" / "Ticket-10001.pdf"
    sidecar = target.with_suffix(".pdf.json")
    original_move = ticket_storage.move_file_within_root

    def fail_sidecar_move(src: Path, dst: Path, *_args: Any, **_kwargs: Any) -> None:
        if dst == sidecar:
            raise OSError("sidecar move failed")
        original_move(src, dst, *_args, **_kwargs)

    monkeypatch.setattr(ticket_storage, "move_file_within_root", fail_sidecar_move)

    with pytest.raises(OSError, match="sidecar move failed"):
        ticket_storage.store_ticket_files(
            pdf_bytes=b"%PDF",
            snapshot=_snapshot(with_attachment=False),
            target_path=target,
            sidecar_path=sidecar,
            ticket_id=100,
            now=datetime(2025, 1, 1, tzinfo=UTC),
            settings=settings,
        )

    assert not target.exists()
    assert not sidecar.exists()
    assert not list(target.parent.glob(".tmp-archiving-*"))


def test_write_attachments_without_payloads_does_not_create_temp_dir(
    tmp_path: Path,
) -> None:
    entries = ticket_storage._write_attachments(
        tmp_path,
        _snapshot(with_attachment=False),
        tmp_path,
        tmp_path / "attachments",
        fsync=False,
    )

    assert entries == []
    assert not (tmp_path / "attachments").exists()
