from __future__ import annotations

from datetime import UTC, datetime
from html import escape
from typing import TYPE_CHECKING, Any

from zammad_pdf_archiver.adapters.zammad.models import Article as ZammadArticle
from zammad_pdf_archiver.adapters.zammad.models import TagList
from zammad_pdf_archiver.adapters.zammad.models import Ticket as ZammadTicket
from zammad_pdf_archiver.domain.snapshot_models import (
    Article,
    AttachmentMeta,
    PartyRef,
    Snapshot,
    TicketMeta,
)

if TYPE_CHECKING:
    from zammad_pdf_archiver.adapters.zammad.client import AsyncZammadClient


def _party_from_zammad_ref(ref: Any) -> PartyRef | None:
    if ref is None:
        return None
    return PartyRef(
        id=getattr(ref, "id", None),
        login=getattr(ref, "login", None),
        email=getattr(ref, "email", None),
        name=getattr(ref, "name", None),
    )


def _article_body_html_and_text(article: ZammadArticle) -> tuple[str, str]:
    body_raw = article.body if isinstance(article.body, str) else ""
    return (escape(body_raw, quote=False) if isinstance(body_raw, str) else ""), body_raw


def _attachment_to_meta(article: ZammadArticle, attachment: Any) -> AttachmentMeta:
    attachment_id = getattr(attachment, "id", None)
    return AttachmentMeta(
        article_id=article.id,
        attachment_id=attachment_id if isinstance(attachment_id, int) else None,
        filename=getattr(attachment, "filename", None),
        size=getattr(attachment, "size", None),
        content_type=getattr(attachment, "content_type", None),
    )


def _article_attachments(article: ZammadArticle) -> list[AttachmentMeta]:
    if not isinstance(article.attachments, list):
        return []
    return [_attachment_to_meta(article, attachment) for attachment in article.attachments]


def _article_to_snapshot(article: ZammadArticle) -> Article:
    body_html, body_text = _article_body_html_and_text(article)
    return Article(
        id=article.id,
        created_at=article.created_at,
        internal=bool(article.internal) if article.internal is not None else False,
        sender=article.from_ or article.to,
        subject=article.subject,
        body_html=body_html,
        body_text=body_text,
        attachments=_article_attachments(article),
    )


def _sort_key(article: Article) -> tuple[bool, datetime, int]:
    sentinel = datetime.max.replace(tzinfo=UTC)
    created = article.created_at
    if created is not None and created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    created = created or sentinel
    return (article.created_at is None, created, article.id)


async def build_snapshot(
    client: AsyncZammadClient,
    ticket_id: int,
    *,
    ticket: ZammadTicket | None = None,
    tags: TagList | None = None,
) -> Snapshot:
    if ticket is None:
        ticket = await client.get_ticket(ticket_id)
    if tags is None:
        tags = await client.list_tags(ticket_id)

    articles = await client.list_articles(ticket_id)
    snapshot_articles = [_article_to_snapshot(article) for article in articles]
    snapshot_articles.sort(key=_sort_key)

    custom_fields = (
        ticket.preferences.custom_fields
        if ticket.preferences is not None
        and isinstance(ticket.preferences.custom_fields, dict)
        else {}
    )

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
            custom_fields=custom_fields,
        ),
        articles=snapshot_articles,
    )
