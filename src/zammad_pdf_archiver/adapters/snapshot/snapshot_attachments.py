from __future__ import annotations

from typing import Any

import structlog

from zammad_pdf_archiver.domain.snapshot_models import Article, AttachmentMeta, Snapshot

log = structlog.get_logger(__name__)


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
        return snapshot_with_omitted_attachment_content(
            snapshot,
            reason="binary_inclusion_disabled",
        )
    if max_total_attachment_bytes <= 0:
        return snapshot_with_omitted_attachment_content(
            snapshot,
            reason="total_budget_not_positive",
        )

    return await snapshot_with_fetched_attachment_content(
        snapshot=snapshot,
        client=client,
        max_attachment_bytes_per_file=max_attachment_bytes_per_file,
        max_total_attachment_bytes=max_total_attachment_bytes,
    )


async def snapshot_with_fetched_attachment_content(
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
            updated, bytes_added, budget_exhausted = await attachment_with_content_budget(
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


async def attachment_with_content_budget(
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
    omission_reason, budget_exhausted = prefetch_attachment_omission_reason(
        att,
        budget_exhausted=budget_exhausted,
        remaining_budget=remaining_budget,
        max_attachment_bytes_per_file=max_attachment_bytes_per_file,
    )
    bytes_added = 0
    if omission_reason is None:
        attachment_id = required_attachment_id(att)
        raw = await fetch_attachment_content(
            client,
            ticket_id=ticket_id,
            article_id=article_id,
            attachment_id=attachment_id,
        )
        content, omission_reason, budget_exhausted, bytes_added = fetched_attachment_outcome(
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


def required_attachment_id(att: AttachmentMeta) -> int:
    if att.attachment_id is None:
        raise RuntimeError("attachment id unexpectedly missing after prefetch validation")
    return att.attachment_id


async def fetch_attachment_content(
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


def fetched_attachment_outcome(
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


def prefetch_attachment_omission_reason(
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


def snapshot_with_omitted_attachment_content(snapshot: Snapshot, *, reason: str) -> Snapshot:
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
