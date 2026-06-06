"""Tests for ticket_storage: attachment writing and failure propagation."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

import zammad_pdf_archiver.app.jobs.ticket_storage as ticket_storage_module
from test.support.checks import check
from test.support.logging_helpers import CapturingWarningLog as _CapturingLog
from test.support.ticket_storage_helpers import (
    make_ticket_storage_settings as _make_settings,
)
from test.support.ticket_storage_helpers import (
    snapshot_no_attachments as _make_snapshot_no_attachments,
)
from test.support.ticket_storage_helpers import (
    snapshot_with_attachments as _make_snapshot_with_attachments,
)
from test.support.ticket_storage_helpers import (
    snapshot_with_skipped_attachments as _make_snapshot_with_skipped_attachments,
)
from zammad_pdf_archiver.app.jobs.ticket_storage import (
    _write_attachments,
    store_ticket_files,
)
from zammad_pdf_archiver.domain.snapshot_models import (
    Article,
    AttachmentMeta,
    Snapshot,
    TicketMeta,
)

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

        entries = _write_attachments(work_dir, snapshot, storage_root, attachments_dir, fsync=False)

        # Should return one entry per attachment with content.
        check(not not len(entries) == 2, "assertion failed")

        # Verify files exist in the temp attachments sub-directory.
        temp_att_dir = storage_root / "work" / "attachments"
        check(not not temp_att_dir.is_dir(), "assertion failed")
        written_files = sorted(f.name for f in temp_att_dir.iterdir())
        check(not not len(written_files) == 2, "assertion failed")

        # Check audit metadata structure.
        for entry in entries:
            check(not "sha256" not in entry, "assertion failed")
            check(not "article_id" not in entry, "assertion failed")
            check(not "attachment_id" not in entry, "assertion failed")
            check(not "storage_path" not in entry, "assertion failed")
            check(not "filename" not in entry, "assertion failed")

    def test_write_attachments_no_articles(self, tmp_path: Path) -> None:
        """Snapshot with no articles returns empty list immediately."""
        ticket = TicketMeta(id=300, number="30001", title="Empty")
        snapshot = Snapshot(ticket=ticket, articles=[])
        entries = _write_attachments(tmp_path, snapshot, tmp_path, tmp_path / "att", fsync=False)
        check(not not entries == [], "assertion failed")

    def test_write_attachments_no_binary_content(self, tmp_path: Path) -> None:
        """Articles whose attachments have content=None are skipped."""
        att = AttachmentMeta(article_id=40, attachment_id=1, filename="x.bin", content=None)
        article = Article(id=40, attachments=[att])
        ticket = TicketMeta(id=400, number="40001", title="No content")
        snapshot = Snapshot(ticket=ticket, articles=[article])

        entries = _write_attachments(tmp_path, snapshot, tmp_path, tmp_path / "att", fsync=False)
        check(not not entries == [], "assertion failed")


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
        target_path = target_dir / "Ticket-10001_2025-01-01.pdf"
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
                    target_path=target_path,
                    ticket_id=100,
                    now=now,
                    settings=settings,
                )


# ===================================================================
# 3. _backup_if_exists — file already present
# ===================================================================


def test_backup_if_exists_renames_existing_file(tmp_path: Path) -> None:
    """_backup_if_exists should rename an existing file to *.bak.<timestamp>."""
    from zammad_pdf_archiver.app.jobs.ticket_storage import _backup_if_exists

    root = tmp_path / "storage"
    root.mkdir()
    target = root / "archive.pdf"
    target.write_bytes(b"original")

    _backup_if_exists(target, storage_root=root, fsync=False)

    check(not not not target.exists(), "assertion failed")
    bak_files = list(root.glob("archive.pdf.bak.*"))
    check(not not len(bak_files) == 1, "assertion failed")
    check(not not bak_files[0].read_bytes() == b"original", "assertion failed")


def test_backup_if_exists_noop_when_no_file(tmp_path: Path) -> None:
    """_backup_if_exists should be a no-op when the target does not exist."""
    from zammad_pdf_archiver.app.jobs.ticket_storage import _backup_if_exists

    root = tmp_path / "storage"
    root.mkdir()
    target = root / "nonexistent.pdf"

    check(
        not _backup_if_exists(target, storage_root=root, fsync=False) is not None,
        "assertion failed",
    )


# ===================================================================
# 4. store_ticket_files — happy path with attachments (covers commit path)
# ===================================================================


def test_store_ticket_files_happy_path_with_attachments(tmp_path: Path) -> None:
    """store_ticket_files succeeds when attachments are present (exercises commit branch)."""
    settings = _make_settings(tmp_path)
    snapshot = _make_snapshot_with_attachments()

    target_dir = tmp_path / "archive" / "user"
    target_dir.mkdir(parents=True)
    target_path = target_dir / "Ticket-10001_2025-01-01.pdf"
    sidecar_path = target_path.with_name(target_path.name + ".json")
    now = datetime(2025, 1, 1, tzinfo=UTC)

    result = store_ticket_files(
        pdf_bytes=b"%PDF-1.4 fake",
        snapshot=snapshot,
        target_path=target_path,
        ticket_id=100,
        now=now,
        settings=settings,
    )

    check(not not result.target_path == target_path, "assertion failed")
    check(not not result.sidecar_path == sidecar_path, "assertion failed")
    check(not not target_path.is_file(), "assertion failed")
    check(not not sidecar_path.is_file(), "assertion failed")
    audit = json.loads(sidecar_path.read_text("utf-8"))
    check(
        not not audit["attachment_summary"]
        == {"total": 2, "written": 2, "metadata_only": 0, "skipped": 0, "skipped_reasons": {}},
        "assertion failed",
    )
    check(not not len(audit["attachments"]) == 2, "assertion failed")


def test_store_ticket_files_logs_staging_cleanup_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _make_settings(tmp_path)
    snapshot = _make_snapshot_no_attachments()
    target_dir = tmp_path / "archive" / "user"
    target_dir.mkdir(parents=True)
    target_path = target_dir / "Ticket-20001_2025-01-01.pdf"
    sidecar_path = target_path.with_name(target_path.name + ".json")
    cleanup_paths: list[Path] = []
    capture = _CapturingLog()

    def _failing_rmtree(path: str | Path) -> None:
        cleanup_paths.append(Path(path))
        raise OSError("cleanup failed")

    monkeypatch.setattr(ticket_storage_module.shutil, "rmtree", _failing_rmtree)
    monkeypatch.setattr(ticket_storage_module, "log", capture)

    result = store_ticket_files(
        pdf_bytes=b"%PDF-1.4 fake",
        snapshot=snapshot,
        target_path=target_path,
        ticket_id=200,
        now=datetime(2025, 1, 1, tzinfo=UTC),
        settings=settings,
    )

    check(not not result.target_path == target_path, "assertion failed")
    check(not not target_path.exists(), "assertion failed")
    check(not not sidecar_path.exists(), "assertion failed")
    check(not not len(cleanup_paths) == 1, "assertion failed")
    check(not not cleanup_paths[0].parent == target_dir, "assertion failed")
    check(not not cleanup_paths[0].name.startswith(".tmp-archiving-200-"), "assertion failed")
    check(
        not not capture.warning_events
        == [("staging_dir_cleanup_failed", {"path": str(cleanup_paths[0]), "exc_info": True})],
        "assertion failed",
    )


def test_store_ticket_files_sidecar_records_skipped_attachment_summary(tmp_path: Path) -> None:
    """Successful archives must report attachment binaries omitted by policy."""
    settings = _make_settings(tmp_path)
    snapshot = _make_snapshot_with_skipped_attachments()

    target_dir = tmp_path / "archive" / "user"
    target_dir.mkdir(parents=True)
    target_path = target_dir / "Ticket-50001_2025-01-01.pdf"
    sidecar_path = target_path.with_name(target_path.name + ".json")

    store_ticket_files(
        pdf_bytes=b"%PDF-1.4 fake",
        snapshot=snapshot,
        target_path=target_path,
        ticket_id=500,
        now=datetime(2025, 1, 1, tzinfo=UTC),
        settings=settings,
    )

    audit = json.loads(sidecar_path.read_text("utf-8"))
    check(
        not not audit["attachment_summary"]
        == {
            "total": 3,
            "written": 1,
            "metadata_only": 2,
            "skipped": 2,
            "skipped_reasons": {"per_file_limit_declared_size": 1, "total_budget_exhausted": 1},
        },
        "assertion failed",
    )
    check(
        not not [entry["filename"] for entry in audit["attachments"]] == ["kept.txt"],
        "assertion failed",
    )
