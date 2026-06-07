from __future__ import annotations

from typing import Any

import structlog

from zammad_pdf_archiver.adapters.snapshot.snapshot_articles import (
    article_to_snapshot as _article_to_snapshot,
)
from zammad_pdf_archiver.adapters.snapshot.snapshot_articles import (
    party_from_zammad_ref as _party_from_zammad_ref,
)
from zammad_pdf_archiver.adapters.snapshot.snapshot_articles import sort_key as _sort_key
from zammad_pdf_archiver.adapters.snapshot.snapshot_attachments import enrich_attachment_content
from zammad_pdf_archiver.adapters.snapshot.snapshot_html import HTMLToText as _HTMLToText
from zammad_pdf_archiver.adapters.snapshot.snapshot_html import (
    strip_html_to_text as _strip_html_to_text_impl,
)
from zammad_pdf_archiver.adapters.zammad.models import Article as ZammadArticle
from zammad_pdf_archiver.adapters.zammad.models import TagList
from zammad_pdf_archiver.adapters.zammad.models import Ticket as ZammadTicket
from zammad_pdf_archiver.domain.snapshot_models import Snapshot, TicketMeta
from zammad_pdf_archiver.domain.ticket_utils import ticket_custom_fields

log = structlog.get_logger(__name__)

__all__ = [
    "_HTMLToText",
    "_strip_html_to_text",
    "build_snapshot",
    "enrich_attachment_content",
]


def _strip_html_to_text(html: str) -> str:
    return _strip_html_to_text_impl(html, parser_cls=_HTMLToText, log=log)


async def build_snapshot(
    client: Any,
    ticket_id: int,
    *,
    ticket: ZammadTicket | None = None,
    tags: TagList | None = None,
) -> Snapshot:
    """Fetch ticket, tags, and articles from Zammad and assemble them into a Snapshot."""
    if ticket is None:
        ticket = await client.get_ticket(ticket_id)
    if tags is None:
        tags = await client.list_tags(ticket_id)

    articles: list[ZammadArticle] = await client.list_articles(ticket_id)

    snapshot_articles = [_article_to_snapshot(a, log=log) for a in articles]
    snapshot_articles.sort(key=_sort_key)

    return Snapshot(
        ticket=TicketMeta(
            id=ticket.id,
            number=ticket.number,
            title=ticket.title,
            created_at=ticket.created_at,
            updated_at=ticket.updated_at,
            customer=_party_from_zammad_ref(ticket.customer),
            owner=_party_from_zammad_ref(ticket.owner),
            tags=list(tags.root),
            custom_fields=ticket_custom_fields(ticket),
        ),
        articles=snapshot_articles,
    )
