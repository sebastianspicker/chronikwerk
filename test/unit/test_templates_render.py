from __future__ import annotations

from datetime import UTC, datetime

from zammad_pdf_archiver.adapters.pdf.template_engine import render_html
from zammad_pdf_archiver.domain.snapshot_models import Article, AttachmentMeta, Snapshot, TicketMeta


def test_default_template_renders_example_snapshot() -> None:
    snapshot = Snapshot(
        ticket=TicketMeta(
            id=1,
            number="T1",
            title="Printer-friendly rendering",
            created_at=datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
        ),
        articles=[
            Article(
                id=100,
                created_at=datetime(2024, 1, 1, 10, 5, tzinfo=UTC),
                body_html="Hello",
                body_text="Hello",
                attachments=[
                    AttachmentMeta(article_id=100, attachment_id=10, filename="invoice.pdf")
                ],
            )
        ],
    )

    html = render_html(snapshot)

    assert "Ticket T1" in html
    assert "Hello" in html
