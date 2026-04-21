"""Tests for ticket_storage: attachment writing and failure propagation."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from zammad_pdf_archiver.app.jobs.ticket_storage import (
    StoragePaths,
    _write_attachments,
    store_ticket_files,
)
from zammad_pdf_archiver.config.settings import Settings
from zammad_pdf_archiver.domain.snapshot_models import (
    Article,
    AttachmentMeta,
    Snapshot,
    TicketMeta,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_settings(tmp_path: Path) -> Settings:
    return Settings.from_mapping(
        {
            "zammad": {"base_url": "https://zammad.example.local", "api_token": "tok"},
            "storage": {"root": str(tmp_path), "fsync": False},
            "hardening": {
                "webhook": {
                    "allow_unsigned": True,
                    "allow_unsigned_when_no_secret": True,
                }
            },
        }
    )


def _make_snapshot_with_attachments() -> Snapshot:
    """Build a Snapshot that contains articles with binary attachments."""
    att1 = AttachmentMeta(
        article_id=10,
        attachment_id=1,
        filename="report.pdf",
        size=4,
        content=b"data",
    )
    att2 = AttachmentMeta(
        article_id=10,
        attachment_id=2,
        filename="image.png",
        size=3,
        content=b"img",
    )
    article = Article(id=10, attachments=[att1, att2])
    ticket = TicketMeta(id=100, number="10001", title="Test Ticket")
    return Snapshot(ticket=ticket, articles=[article])


def _make_snapshot_no_attachments() -> Snapshot:
    article = Article(id=20, attachments=[])
    ticket = TicketMeta(id=200, number="20001", title="No Attachments")
    return Snapshot(ticket=ticket, articles=[article])


# ===================================================================
# 1. _write_attachments — happy path
# ===================================================================


class TestWriteAttachments:
    """Verify _write_attachments writes files and returns audit metadata."""

    def test_write_attachments_with_attachments(self, tmp_path: Path) -> None:
        """Attachments with content are written to the temp dir; audit entries are returned."""
        snapshot = _make_snapshot_with_attachments()
        storage_root = tmp_path / "storage"
        storage_root.mkdir()
        # work_dir must be under storage_root so write_bytes path validation passes.
        work_dir = storage_root / "work"
        work_dir.mkdir()
        attachments_dir = storage_root / "attachments"

        entries = _write_attachments(
            work_dir, snapshot, storage_root, attachments_dir, fsync=False
        )

        # Should return one entry per attachment with content.
        assert len(entries) == 2

        # Verify files exist in the temp attachments sub-directory.
        temp_att_dir = storage_root / "work" / "attachments"
        assert temp_att_dir.is_dir()
        written_files = sorted(f.name for f in temp_att_dir.iterdir())
        assert len(written_files) == 2

        # Check audit metadata structure.
        for entry in entries:
            assert "sha256" in entry
            assert "article_id" in entry
            assert "attachment_id" in entry
            assert "storage_path" in entry
            assert "filename" in entry

    def test_write_attachments_no_articles(self, tmp_path: Path) -> None:
        """Snapshot with no articles returns empty list immediately."""
        ticket = TicketMeta(id=300, number="30001", title="Empty")
        snapshot = Snapshot(ticket=ticket, articles=[])
        entries = _write_attachments(
            tmp_path, snapshot, tmp_path, tmp_path / "att", fsync=False
        )
        assert entries == []

    def test_write_attachments_no_binary_content(self, tmp_path: Path) -> None:
        """Articles whose attachments have content=None are skipped."""
        att = AttachmentMeta(article_id=40, attachment_id=1, filename="x.bin", content=None)
        article = Article(id=40, attachments=[att])
        ticket = TicketMeta(id=400, number="40001", title="No content")
        snapshot = Snapshot(ticket=ticket, articles=[article])

        entries = _write_attachments(
            tmp_path, snapshot, tmp_path, tmp_path / "att", fsync=False
        )
        assert entries == []


# ===================================================================
# 2. store_ticket_files — attachment write failure propagation
# ===================================================================


class TestStoreTicketFilesAttachmentWriteFailure:
    """Ensure OSError during attachment writing propagates to the caller."""

    def test_store_ticket_files_attachment_write_failure(self, tmp_path: Path) -> None:
        """OSError during attachment write_bytes bubbles up from store_ticket_files."""
        settings = _make_settings(tmp_path)
        snapshot = _make_snapshot_with_attachments()

        target_dir = tmp_path / "archive" / "user"
        target_dir.mkdir(parents=True)
        paths = StoragePaths(
            target_dir=target_dir,
            target_path=target_dir / "Ticket-10001_2025-01-01.pdf",
            sidecar_path=target_dir / "Ticket-10001_2025-01-01.pdf.json",
        )
        now = datetime(2025, 1, 1, tzinfo=UTC)

        # Track calls so we only fail on the attachment write (not the PDF write).
        call_count = 0
        original_write_bytes = __import__(
            "zammad_pdf_archiver.adapters.storage.fs_storage", fromlist=["write_bytes"]
        ).write_bytes

        def _failing_write_bytes(target_path: Path, data: bytes, **kwargs: object) -> None:
            nonlocal call_count
            call_count += 1
            # The first write_bytes call inside store_ticket_files is the attachment.
            if call_count <= 2:  # two attachments
                raise OSError("disk full")
            return original_write_bytes(target_path, data, **kwargs)

        with patch(
            "zammad_pdf_archiver.app.jobs.ticket_storage.write_bytes",
            side_effect=_failing_write_bytes,
        ):
            with pytest.raises(OSError, match="disk full"):
                store_ticket_files(
                    pdf_bytes=b"%PDF-fake",
                    snapshot=snapshot,
                    paths=paths,
                    ticket_id=100,
                    now=now,
                    settings=settings,
                )
