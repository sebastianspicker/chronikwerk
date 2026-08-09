"""Renders the default PDF template and verifies output metadata and warnings."""

from __future__ import annotations

import asyncio
import logging
import warnings
from io import BytesIO

from chronikwerk.adapters.pdf.render_pdf import render_pdf
from chronikwerk.domain.snapshot_models import Snapshot


def _rendering_snapshot() -> Snapshot:
    """Build a deterministic rendering snapshot fixture for focused assertions."""
    return Snapshot.model_validate(
        {
            "ticket": {
                "id": 1,
                "number": "T1",
                "title": "PDF rendering integration test",
                "created_at": "2024-01-01T10:00:00Z",
                "updated_at": "2024-01-02T12:30:00Z",
                "customer": {"name": "Acme Corp", "email": "support@acme.invalid"},
                "owner": {"login": "agent1", "name": "Agent One"},
                "tags": ["pdf:sign", "billing"],
                "custom_fields": {
                    "archive_path": ["ACME", "2024", "Invoices"],
                    "archive_user_mode": "owner",
                },
            },
            "articles": [_customer_article(), _internal_article()],
        }
    )


def _customer_article() -> dict[str, object]:
    """Build a deterministic customer article fixture for focused assertions."""
    return {
        "id": 100,
        "created_at": "2024-01-01T10:05:00Z",
        "internal": False,
        "sender": "customer@acme.invalid",
        "subject": "Initial request",
        "body_html": "<p>Hello <strong>World</strong></p>",
        "body_text": "Hello World",
        "attachments": [
            {
                "article_id": 100,
                "attachment_id": 10,
                "filename": "invoice.pdf",
                "size": 12345,
                "content_type": "application/pdf",
            }
        ],
    }


def _internal_article() -> dict[str, object]:
    """Build a deterministic internal article fixture for focused assertions."""
    return {
        "id": 101,
        "created_at": "2024-01-01T11:00:00Z",
        "internal": True,
        "sender": "agent1@acme.invalid",
        "subject": "Internal note",
        "body_html": "<p>Internal note.</p>",
        "body_text": "Internal note.",
        "attachments": [],
    }


def _warning_snapshot(ticket_id: int, title: str) -> Snapshot:
    """Build the minimal snapshot used by renderer warning guards."""
    return Snapshot.model_validate(
        {
            "ticket": {
                "id": ticket_id,
                "number": f"T{ticket_id}",
                "title": title,
                "created_at": "2024-01-01T00:00:00Z",
                "updated_at": "2024-01-01T00:00:00Z",
                "tags": ["pdf:sign"],
                "custom_fields": {"archive_path": ["A"], "archive_user_mode": "owner"},
            },
            "articles": [],
        }
    )


def test_render_pdf_default_template_produces_pdf_bytes() -> None:
    pdf_bytes = asyncio.run(render_pdf(_rendering_snapshot()))

    assert pdf_bytes.startswith(b"%PDF")
    assert len(pdf_bytes) > 5_000


def test_render_pdf_is_tagged_and_has_bcp47_language() -> None:
    from pyhanko.pdf_utils.reader import PdfFileReader

    pdf_bytes = asyncio.run(render_pdf(_rendering_snapshot(), locale="en_GB"))
    reader = PdfFileReader(BytesIO(pdf_bytes))

    assert str(reader.root["/Lang"]) == "en-GB"
    assert bool(reader.root["/MarkInfo"]["/Marked"]) is True
    assert reader.root.get("/Outlines") is not None


def test_render_pdf_does_not_emit_pydyf_identifier_deprecation_warning() -> None:
    snapshot = _warning_snapshot(2, "warning guard")

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        pdf_bytes = asyncio.run(render_pdf(snapshot))

    assert pdf_bytes.startswith(b"%PDF")
    assert not any(
        (
            isinstance(item.message, DeprecationWarning)
            and "PDF objects don’t take version or identifier" in str(item.message)
        )
        for item in caught
    )


def test_render_pdf_default_template_avoids_ignored_css_warnings(caplog) -> None:
    snapshot = _warning_snapshot(3, "css warning guard")

    with caplog.at_level(logging.WARNING, logger="weasyprint"):
        pdf_bytes = asyncio.run(render_pdf(snapshot))

    assert pdf_bytes.startswith(b"%PDF")
    assert not any(
        "Ignored `" in rec.getMessage() and "invalid value" in rec.getMessage()
        for rec in caplog.records
    )
    assert not any(
        "Ignored `" in rec.getMessage() and "unknown property" in rec.getMessage()
        for rec in caplog.records
    )
