from __future__ import annotations

import re
from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import Any

import structlog

from zammad_pdf_archiver.adapters.zammad.models import Article as ZammadArticle
from zammad_pdf_archiver.adapters.zammad.models import TagList
from zammad_pdf_archiver.adapters.zammad.models import Ticket as ZammadTicket
from zammad_pdf_archiver.domain.html_sanitize import sanitize_html_fragment
from zammad_pdf_archiver.domain.snapshot_models import (
    Article,
    AttachmentMeta,
    PartyRef,
    Snapshot,
    TicketMeta,
)
from zammad_pdf_archiver.domain.ticket_utils import ticket_custom_fields

log = structlog.get_logger(__name__)

_HTML_TAG_HINT_RE = re.compile(
    r"<\s*(?:p|div|br|span|a|ul|ol|li|pre|code|blockquote|table|tr|td|th|strong|em|b|i|u)\b",
    re.IGNORECASE,
)


class _HTMLToText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self._skip_depth += 1
            return
        if tag in {"p", "div", "br", "li", "tr"} and self._skip_depth == 0:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self._skip_depth > 0:
            self._skip_depth -= 1
            return
        if tag in {"p", "div", "li", "tr"} and self._skip_depth == 0:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            self._parts.append(data)

    def get_text(self) -> str:
        text = "".join(self._parts)
        # Normalize whitespace without being too opinionated about newlines.
        text = "\n".join(line.strip() for line in text.splitlines())
        text = "\n".join(line for line in text.splitlines() if line)
        return text.strip()


def _strip_html_to_text(html: str) -> str:
    try:
        parser = _HTMLToText()
        parser.feed(html)
        parser.close()
        return parser.get_text()
    except Exception:
        log.warning("html_strip_failed", exc_info=True)
        return ""


def _has_html_hint(*, content_type: str | None, body: str) -> bool:
    """Detect HTML content via content-type header or a heuristic tag-pattern match."""
    if content_type and "html" in content_type.lower():
        return True
    # Heuristic: only treat bodies as HTML if they look like common HTML tags.
    return bool(_HTML_TAG_HINT_RE.search(body))


def _party_from_zammad_ref(ref: Any) -> PartyRef | None:
    if ref is None:
        return None
    return PartyRef(
        id=getattr(ref, "id", None),
        login=getattr(ref, "login", None),
        email=getattr(ref, "email", None),
        name=getattr(ref, "name", None),
    )


def _article_to_snapshot(article: ZammadArticle) -> Article:
    """Convert a Zammad API article into a domain-layer Article with sanitized HTML/text."""
    body_html, body_text = _article_body_fields(article)

    return Article(
        id=article.id,
        created_at=article.created_at,
        internal=bool(article.internal) if article.internal is not None else False,
        sender=article.from_ or article.to,
        subject=article.subject,
        body_html=body_html,
        body_text=body_text,
        attachments=_article_attachment_metadata(article),
    )


def _article_body_fields(article: ZammadArticle) -> tuple[str, str]:
    body_raw = article.body if isinstance(article.body, str) else ""
    if not body_raw:
        return "", ""
    if not _has_html_hint(content_type=article.content_type, body=body_raw):
        return "", body_raw

    body_html = sanitize_html_fragment(body_raw)
    body_text = _strip_html_to_text(body_html) if body_html else _strip_html_to_text(body_raw)
    return body_html, body_text or body_raw


def _article_attachment_metadata(article: ZammadArticle) -> list[AttachmentMeta]:
    if not isinstance(article.attachments, list):
        return []
    return [_attachment_metadata(article.id, attachment) for attachment in article.attachments]


def _attachment_metadata(article_id: int, attachment: Any) -> AttachmentMeta:
    attachment_id = getattr(attachment, "id", None)
    return AttachmentMeta(
        article_id=article_id,
        attachment_id=attachment_id if isinstance(attachment_id, int) else None,
        filename=getattr(attachment, "filename", None),
        size=getattr(attachment, "size", None),
        content_type=getattr(attachment, "content_type", None),
    )


def _sort_key(article: Article) -> tuple[bool, datetime, int]:
    """Sort articles chronologically, pushing those without a timestamp to the end."""
    sentinel = datetime.max.replace(tzinfo=UTC)
    created = article.created_at
    if created is not None and created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    created = created or sentinel
    return (article.created_at is None, created, article.id)


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

    snapshot_articles = [_article_to_snapshot(a) for a in articles]
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


async def enrich_attachment_content(
    snapshot: Snapshot,
    client: Any,
    *,
    include_attachment_binary: bool,
    max_attachment_bytes_per_file: int,
    max_total_attachment_bytes: int,
) -> Snapshot:
    """Fetch attachment binaries and record why any binary payload was omitted."""
    if not include_attachment_binary:
        return _snapshot_with_omitted_attachment_content(
            snapshot,
            reason="binary_inclusion_disabled",
        )
    if max_total_attachment_bytes <= 0:
        return _snapshot_with_omitted_attachment_content(
            snapshot,
            reason="total_budget_not_positive",
        )

    return await _snapshot_with_fetched_attachment_content(
        snapshot=snapshot,
        client=client,
        max_attachment_bytes_per_file=max_attachment_bytes_per_file,
        max_total_attachment_bytes=max_total_attachment_bytes,
    )


async def _snapshot_with_fetched_attachment_content(
    *,
    snapshot: Snapshot,
    client: Any,
    max_attachment_bytes_per_file: int,
    max_total_attachment_bytes: int,
) -> Snapshot:
    """Fetch attachment binaries without exceeding the configured total byte budget."""
    ticket_id = snapshot.ticket.id
    total_so_far = 0
    budget_exhausted = False
    new_articles: list[Article] = []
    for article in snapshot.articles:
        new_attachments: list[AttachmentMeta] = []
        for att in article.attachments:
            updated, bytes_added, budget_exhausted = await _attachment_with_content_budget(
                att,
                client=client,
                ticket_id=ticket_id,
                article_id=article.id,
                budget_exhausted=budget_exhausted,
                remaining_budget=max_total_attachment_bytes - total_so_far,
                max_attachment_bytes_per_file=max_attachment_bytes_per_file,
            )
            total_so_far += bytes_added
            new_attachments.append(updated)
        new_articles.append(article.model_copy(update={"attachments": new_attachments}))
    return Snapshot(ticket=snapshot.ticket, articles=new_articles)


async def _attachment_with_content_budget(
    att: AttachmentMeta,
    *,
    client: Any,
    ticket_id: int,
    article_id: int,
    budget_exhausted: bool,
    remaining_budget: int,
    max_attachment_bytes_per_file: int,
) -> tuple[AttachmentMeta, int, bool]:
    content: bytes | None = None
    omission_reason, budget_exhausted = _prefetch_attachment_omission_reason(
        att,
        budget_exhausted=budget_exhausted,
        remaining_budget=remaining_budget,
        max_attachment_bytes_per_file=max_attachment_bytes_per_file,
    )
    bytes_added = 0
    if omission_reason is None:
        attachment_id = _required_attachment_id(att)
        raw = await _fetch_attachment_content(
            client,
            ticket_id=ticket_id,
            article_id=article_id,
            attachment_id=attachment_id,
        )
        content, omission_reason, budget_exhausted, bytes_added = _fetched_attachment_outcome(
            raw,
            remaining_budget=remaining_budget,
            max_attachment_bytes_per_file=max_attachment_bytes_per_file,
        )

    return (
        att.model_copy(
            update={
                "content": content,
                "content_omission_reason": omission_reason,
            }
        ),
        bytes_added,
        budget_exhausted,
    )


def _required_attachment_id(att: AttachmentMeta) -> int:
    if att.attachment_id is None:
        raise RuntimeError("attachment id unexpectedly missing after prefetch validation")
    return att.attachment_id


async def _fetch_attachment_content(
    client: Any,
    *,
    ticket_id: int,
    article_id: int,
    attachment_id: int,
) -> bytes:
    try:
        return await client.get_attachment_content(ticket_id, article_id, attachment_id)
    except Exception:
        log.warning(
            "attachment_fetch_failed",
            ticket_id=ticket_id,
            article_id=article_id,
            attachment_id=attachment_id,
            exc_info=True,
        )
        raise


def _fetched_attachment_outcome(
    raw: bytes,
    *,
    remaining_budget: int,
    max_attachment_bytes_per_file: int,
) -> tuple[bytes | None, str | None, bool, int]:
    if len(raw) > max_attachment_bytes_per_file:
        return None, "per_file_limit_fetched_size", False, 0
    if len(raw) > remaining_budget:
        return None, "total_budget_exhausted", True, 0
    return raw, None, False, len(raw)


def _prefetch_attachment_omission_reason(
    att: AttachmentMeta,
    *,
    budget_exhausted: bool,
    remaining_budget: int,
    max_attachment_bytes_per_file: int,
) -> tuple[str | None, bool]:
    if att.attachment_id is None:
        return "missing_attachment_id", budget_exhausted
    if budget_exhausted or remaining_budget <= 0:
        return "total_budget_exhausted", True
    if att.size is not None and att.size > max_attachment_bytes_per_file:
        return "per_file_limit_declared_size", budget_exhausted
    if att.size is not None and att.size > remaining_budget:
        return "total_budget_exhausted", True
    return None, budget_exhausted


def _snapshot_with_omitted_attachment_content(snapshot: Snapshot, *, reason: str) -> Snapshot:
    new_articles: list[Article] = []
    for article in snapshot.articles:
        new_attachments: list[AttachmentMeta] = []
        for att in article.attachments:
            new_attachments.append(
                att.model_copy(
                    update={
                        "content": None,
                        "content_omission_reason": reason,
                    }
                )
            )
        new_articles.append(article.model_copy(update={"attachments": new_attachments}))
    return Snapshot(ticket=snapshot.ticket, articles=new_articles)
