from __future__ import annotations

from datetime import UTC, datetime

from test.support.checks import check
from zammad_pdf_archiver.adapters.pdf.template_engine import render_html
from zammad_pdf_archiver.domain.snapshot_models import (
    Article,
    AttachmentMeta,
    PartyRef,
    Snapshot,
    TicketMeta,
)


def test_default_template_renders_example_snapshot() -> None:
    snapshot = Snapshot(
        ticket=TicketMeta(
            id=1,
            number="T1",
            title="Printer-friendly rendering",
            created_at=datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
            updated_at=datetime(2024, 1, 2, 12, 30, tzinfo=UTC),
            customer=PartyRef(name="Acme Corp", email="support@acme.invalid"),
            owner=PartyRef(login="agent1", name="Agent One"),
            tags=["pdf:sign", "billing"],
            custom_fields={
                "archive_path": ["ACME", "2024", "Invoices"],
                "archive_user_mode": "owner",
            },
        ),
        articles=[
            Article(
                id=100,
                created_at=datetime(2024, 1, 1, 10, 5, tzinfo=UTC),
                internal=False,
                sender="customer@acme.invalid",
                subject="Initial request",
                body_html="<p>Hello <strong>World</strong></p>",
                body_text="Hello World",
                attachments=[
                    AttachmentMeta(
                        article_id=100,
                        attachment_id=10,
                        filename="invoice.pdf",
                        size=12345,
                        content_type="application/pdf",
                    )
                ],
            )
        ],
    )

    html = render_html(snapshot, "default")

    check(not "Ticket T1" not in html, "assertion failed")
    check(not "Hello" not in html, "assertion failed")


def test_compact_template_renders_example_snapshot() -> None:
    """Compact template variant renders snapshot via package loader."""
    snapshot = Snapshot(
        ticket=TicketMeta(
            id=1,
            number="T2",
            title="Compact variant",
            created_at=datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
            updated_at=datetime(2024, 1, 2, 12, 30, tzinfo=UTC),
            customer=PartyRef(name="Acme", email="a@b.invalid"),
            owner=PartyRef(login="agent1", name="Agent One"),
            tags=[],
            custom_fields={},
        ),
        articles=[
            Article(
                id=101,
                created_at=datetime(2024, 1, 1, 11, 0, tzinfo=UTC),
                internal=True,
                sender="agent1",
                subject="Note",
                body_html="<p>Compact body</p>",
                body_text="Compact body",
                attachments=[],
            )
        ],
    )
    html = render_html(snapshot, "compact")
    check(not "Ticket T2" not in html, "assertion failed")
    check(not "Compact body" not in html, "assertion failed")
    check(not "compact" not in html, "assertion failed")
