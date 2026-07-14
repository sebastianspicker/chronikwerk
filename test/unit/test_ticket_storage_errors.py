from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
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


def _snapshot() -> Snapshot:
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
                attachments=[],
            )
        ],
    )


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
            snapshot=_snapshot(),
            target_path=target,
            sidecar_path=sidecar,
            ticket_id=100,
            now=datetime(2025, 1, 1, tzinfo=UTC),
            settings=settings,
        )

    assert not target.exists()
    assert not sidecar.exists()
    assert not list(target.parent.glob(".tmp-archiving-*"))


def test_store_ticket_files_rejects_outside_target_before_creating_temp_directory(
    tmp_path: Path,
) -> None:
    storage_root = tmp_path / "root"
    settings = _settings(storage_root)
    target = tmp_path / "outside" / "Ticket-10001.pdf"
    sidecar = target.with_suffix(".pdf.json")

    with pytest.raises(ValueError, match="escapes root"):
        ticket_storage.store_ticket_files(
            pdf_bytes=b"%PDF",
            snapshot=_snapshot(),
            target_path=target,
            sidecar_path=sidecar,
            ticket_id=100,
            now=datetime(2025, 1, 1, tzinfo=UTC),
            settings=settings,
        )

    assert not target.parent.exists()
