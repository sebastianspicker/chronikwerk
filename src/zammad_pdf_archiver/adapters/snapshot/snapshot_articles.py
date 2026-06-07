from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from zammad_pdf_archiver.adapters.snapshot.snapshot_html import (
    has_html_hint,
    strip_html_to_text,
)
from zammad_pdf_archiver.adapters.zammad.models import Article as ZammadArticle
from zammad_pdf_archiver.domain.html_sanitize import sanitize_html_fragment
from zammad_pdf_archiver.domain.snapshot_models import Article, AttachmentMeta, PartyRef


def party_from_zammad_ref(ref: Any) -> PartyRef | None:
    if ref is None:
        return None
    return PartyRef(
        id=getattr(ref, "id", None),
        login=getattr(ref, "login", None),
        email=getattr(ref, "email", None),
        name=getattr(ref, "name", None),
    )


def article_to_snapshot(article: ZammadArticle, *, log: Any) -> Article:
    """Convert a Zammad API article into a domain-layer Article with sanitized HTML/text."""
    body_html, body_text = article_body_fields(article, log=log)

    return Article(
        id=article.id,
        created_at=article.created_at,
        internal=bool(article.internal) if article.internal is not None else False,
        sender=article.from_ or article.to,
        subject=article.subject,
        body_html=body_html,
        body_text=body_text,
        attachments=article_attachment_metadata(article),
    )


def article_body_fields(article: ZammadArticle, *, log: Any) -> tuple[str, str]:
    body_raw = article.body if isinstance(article.body, str) else ""
    if not body_raw:
        return "", ""
    if not has_html_hint(content_type=article.content_type, body=body_raw):
        return "", body_raw

    body_html = sanitize_html_fragment(body_raw)
    body_text = (
        strip_html_to_text(body_html, log=log)
        if body_html
        else strip_html_to_text(body_raw, log=log)
    )
    return body_html, body_text or body_raw


def article_attachment_metadata(article: ZammadArticle) -> list[AttachmentMeta]:
    if not isinstance(article.attachments, list):
        return []
    return [attachment_metadata(article.id, attachment) for attachment in article.attachments]


def attachment_metadata(article_id: int, attachment: Any) -> AttachmentMeta:
    attachment_id = getattr(attachment, "id", None)
    return AttachmentMeta(
        article_id=article_id,
        attachment_id=attachment_id if isinstance(attachment_id, int) else None,
        filename=getattr(attachment, "filename", None),
        size=getattr(attachment, "size", None),
        content_type=getattr(attachment, "content_type", None),
    )


def sort_key(article: Article) -> tuple[bool, datetime, int]:
    """Sort articles chronologically, pushing those without a timestamp to the end."""
    sentinel = datetime.max.replace(tzinfo=UTC)
    created = article.created_at
    if created is not None and created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    created = created or sentinel
    return (article.created_at is None, created, article.id)
