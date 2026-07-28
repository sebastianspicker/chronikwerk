"""Verifies default templates render localized ticket snapshots completely."""

from __future__ import annotations

from datetime import UTC, datetime

from chronikwerk.adapters.pdf.template_engine import render_html
from chronikwerk.domain.snapshot_models import Article, AttachmentMeta, Snapshot, TicketMeta


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


def test_default_template_localizes_language_and_completeness() -> None:
    snapshot = Snapshot(
        ticket=TicketMeta(id=1, number="T1", title="Coverage"),
        articles=[Article(id=100, body_html="<p>Hello</p>")],
        articles_total=3,
        articles_omitted=2,
    )

    german = render_html(snapshot, locale="de_DE")
    english = render_html(snapshot, locale="en_GB")

    assert '<html lang="de-DE">' in german
    assert "1 von 3 Artikeln" in german
    assert "2 Artikel wurden" in german
    assert '<html lang="en-GB">' in english
    assert "1 of 3 articles" in english
    assert "2 articles were omitted" in english
    assert '<h2 class="article-header">' in english
