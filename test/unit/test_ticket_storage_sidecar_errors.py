"""Ticket storage sidecar failure rollback tests."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from test.support.checks import check
from test.support.ticket_storage_helpers import (
    expect_store_ticket_files_oserror,
    make_ticket_storage_settings,
    snapshot_no_attachments,
    snapshot_with_attachments,
)


def test_store_ticket_files_sidecar_failure_cleans_up_pdf(tmp_path: Path) -> None:
    """When the sidecar move fails, the already-moved PDF is removed to maintain atomicity."""
    settings = make_ticket_storage_settings(tmp_path)
    snapshot = snapshot_no_attachments()

    target_dir = tmp_path / "archive" / "user"
    target_dir.mkdir(parents=True)
    pdf_path = target_dir / "Ticket-20001_2025-01-01.pdf"
    sidecar_path = pdf_path.with_name(pdf_path.name + ".json")
    now = datetime(2025, 1, 1, tzinfo=UTC)

    call_count = 0
    original_move = __import__(
        "zammad_pdf_archiver.adapters.storage.fs_storage", fromlist=["move_file_within_root"]
    ).move_file_within_root

    def _failing_sidecar_move(src: Path, dst: Path, **kwargs: object) -> None:
        nonlocal call_count
        call_count += 1
        if dst == sidecar_path:
            pdf_path.write_bytes(b"%PDF-moved")
            raise OSError("sidecar move failed")
        return original_move(src, dst, **kwargs)

    with patch(
        "zammad_pdf_archiver.app.jobs.ticket_storage.move_file_within_root",
        side_effect=_failing_sidecar_move,
    ):
        expect_store_ticket_files_oserror(
            pdf_path=pdf_path,
            snapshot=snapshot,
            ticket_id=200,
            now=now,
            settings=settings,
        )

    check(not not not pdf_path.exists(), "assertion failed")


def test_store_ticket_files_sidecar_failure_rolls_back_attachments_and_backups(
    tmp_path: Path,
) -> None:
    """Sidecar failure must not leave new attachments or discard previous files."""
    settings = make_ticket_storage_settings(tmp_path)
    snapshot = snapshot_with_attachments()

    target_dir = tmp_path / "archive" / "user"
    target_dir.mkdir(parents=True)
    attachments_dir = target_dir / "attachments"
    attachments_dir.mkdir()
    pdf_path = target_dir / "Ticket-10001_2025-01-01.pdf"
    sidecar_path = target_dir / "Ticket-10001_2025-01-01.pdf.json"
    existing_attachment = attachments_dir / "10_1_report.pdf"
    new_attachment = attachments_dir / "10_2_image.png"

    pdf_path.write_bytes(b"old-pdf")
    sidecar_path.write_bytes(b"old-sidecar")
    existing_attachment.write_bytes(b"old-attachment")

    now = datetime(2025, 1, 1, tzinfo=UTC)
    original_move = __import__(
        "zammad_pdf_archiver.adapters.storage.fs_storage", fromlist=["move_file_within_root"]
    ).move_file_within_root

    def _failing_sidecar_move(src: Path, dst: Path, **kwargs: object) -> None:
        if dst == sidecar_path and src.parent != target_dir:
            raise OSError("sidecar move failed")
        return original_move(src, dst, **kwargs)

    with patch(
        "zammad_pdf_archiver.app.jobs.ticket_storage.move_file_within_root",
        side_effect=_failing_sidecar_move,
    ):
        expect_store_ticket_files_oserror(
            pdf_path=pdf_path,
            snapshot=snapshot,
            ticket_id=100,
            now=now,
            settings=settings,
        )

    check(not not pdf_path.read_bytes() == b"old-pdf", "assertion failed")
    check(not not sidecar_path.read_bytes() == b"old-sidecar", "assertion failed")
    check(not not existing_attachment.read_bytes() == b"old-attachment", "assertion failed")
    check(not not not new_attachment.exists(), "assertion failed")
