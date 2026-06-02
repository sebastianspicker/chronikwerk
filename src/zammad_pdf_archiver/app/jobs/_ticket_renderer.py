"""Build normalized snapshots from Zammad tickets and render them to PDF."""

from __future__ import annotations

import asyncio
from time import perf_counter
from typing import TYPE_CHECKING

import structlog

from zammad_pdf_archiver.adapters.pdf.render_pdf import render_pdf
from zammad_pdf_archiver.adapters.signing.sign_pdf import sign_pdf
from zammad_pdf_archiver.adapters.snapshot.build_snapshot import (
    build_snapshot,
    enrich_attachment_content,
)
from zammad_pdf_archiver.domain.snapshot_models import Snapshot
from zammad_pdf_archiver.observability.metrics import render_seconds, sign_seconds

if TYPE_CHECKING:
    from zammad_pdf_archiver.adapters.zammad.client import AsyncZammadClient
    from zammad_pdf_archiver.adapters.zammad.models import TagList, Ticket
    from zammad_pdf_archiver.config.settings import Settings

log = structlog.get_logger(__name__)


def _apply_article_cap(
    snapshot: Snapshot,
    *,
    ticket_id: int,
    settings: Settings,
) -> tuple[Snapshot, bool]:
    max_articles = settings.pdf.max_articles
    if (
        settings.pdf.article_limit_mode != "cap_and_continue"
        or max_articles <= 0
        or len(snapshot.articles) <= max_articles
    ):
        return snapshot, False

    log.warning(
        "process_ticket.article_limit_capped",
        ticket_id=ticket_id,
        total=len(snapshot.articles),
        cap=max_articles,
    )
    return Snapshot(
        ticket=snapshot.ticket,
        articles=snapshot.articles[:max_articles],
    ), True


def _count_skipped_attachments(snapshot: Snapshot) -> int:
    return sum(
        1
        for article in snapshot.articles
        for attachment in article.attachments
        if attachment.content_omission_reason is not None
    )


def _render_snapshot_pdf(snapshot: Snapshot, settings: Settings) -> bytes:
    render_start = perf_counter()
    pdf_bytes = render_pdf(
        snapshot,
        settings.pdf.template_variant,
        max_articles=settings.pdf.max_articles,
        locale=settings.pdf.locale,
        timezone=settings.pdf.timezone,
        templates_root=settings.pdf.templates_root,
    )
    render_seconds.observe(perf_counter() - render_start)
    return pdf_bytes


async def _maybe_sign_pdf(pdf_bytes: bytes, settings: Settings) -> bytes:
    if not settings.signing.enabled:
        return pdf_bytes

    sign_start = perf_counter()
    # pyHanko's synchronous signing helper uses asyncio.run() internally.
    # Offload to a worker thread to avoid:
    # "asyncio.run() cannot be called from a running event loop".
    trust_env = settings.hardening.transport.trust_env
    signed_pdf = await asyncio.to_thread(sign_pdf, pdf_bytes, settings.signing, trust_env=trust_env)
    sign_seconds.observe(perf_counter() - sign_start)
    return signed_pdf


async def build_and_render_pdf(
    client: AsyncZammadClient,
    ticket: Ticket,
    tags: TagList,
    ticket_id: int,
    settings: Settings,
) -> tuple[bytes, Snapshot, bool, int]:
    """Fetch ticket data, render to PDF, and optionally sign/timestamp."""
    snapshot = await build_snapshot(
        client,
        ticket_id,
        ticket=ticket,
        tags=tags,
    )

    snapshot, articles_capped = _apply_article_cap(
        snapshot,
        ticket_id=ticket_id,
        settings=settings,
    )
    snapshot = await enrich_attachment_content(
        snapshot,
        client,
        include_attachment_binary=settings.pdf.include_attachment_binary,
        max_attachment_bytes_per_file=settings.pdf.max_attachment_bytes_per_file,
        max_total_attachment_bytes=settings.pdf.max_total_attachment_bytes,
    )
    attachments_skipped = _count_skipped_attachments(snapshot)

    pdf_bytes = _render_snapshot_pdf(snapshot, settings)
    pdf_bytes = await _maybe_sign_pdf(pdf_bytes, settings)
    return pdf_bytes, snapshot, articles_capped, attachments_skipped
