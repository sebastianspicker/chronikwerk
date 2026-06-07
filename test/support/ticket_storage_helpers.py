"""Shared helpers for ticket storage tests."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from test.support.credentials import fake_credential
from zammad_pdf_archiver.app.jobs.ticket_storage import store_ticket_files
from zammad_pdf_archiver.config.settings import Settings
from zammad_pdf_archiver.domain.snapshot_models import (
    Article,
    AttachmentMeta,
    Snapshot,
    TicketMeta,
)


def make_ticket_storage_settings(tmp_path: Path) -> Settings:
    return Settings.from_mapping(
        {
            "zammad": {
                "base_url": "https://zammad.example.local",
                "api_token": fake_credential("tok"),
            },
            "storage": {"root": str(tmp_path), "fsync": False},
            "hardening": {
                "webhook": {
                    "allow_unsigned": True,
                    "allow_unsigned_when_no_secret": bool(1),
                }
            },
        }
    )


def snapshot_with_attachments() -> Snapshot:
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


def snapshot_no_attachments() -> Snapshot:
    article = Article(id=20, attachments=[])
    ticket = TicketMeta(id=200, number="20001", title="No Attachments")
    return Snapshot(ticket=ticket, articles=[article])


def snapshot_with_skipped_attachments() -> Snapshot:
    ticket = TicketMeta(id=500, number="50001", title="Skipped attachment")
    attachments = [
        AttachmentMeta(
            article_id=50,
            attachment_id=1,
            filename="kept.txt",
            content=b"kept",
        ),
        AttachmentMeta(
            article_id=50,
            attachment_id=2,
            filename="large.bin",
            content=None,
            content_omission_reason="per_file_limit_declared_size",
        ),
        AttachmentMeta(
            article_id=50,
            attachment_id=3,
            filename="later.bin",
            content=None,
            content_omission_reason="total_budget_exhausted",
        ),
    ]
    return Snapshot(ticket=ticket, articles=[Article(id=50, attachments=attachments)])


def expect_store_ticket_files_oserror(
    *,
    pdf_path: Path,
    snapshot: Snapshot,
    ticket_id: int,
    now: datetime,
    settings: Settings,
) -> None:
    with pytest.raises(OSError, match="sidecar move failed"):
        store_ticket_files(
            pdf_bytes=b"%PDF-1.4 fake",
            snapshot=snapshot,
            target_path=pdf_path,
            ticket_id=ticket_id,
            now=now,
            settings=settings,
        )
