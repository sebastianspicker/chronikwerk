"""Build normalized snapshots from Zammad tickets and render them as PDFs."""
from __future__ import annotations

from time import perf_counter
from typing import TYPE_CHECKING

import structlog

from zammad_pdf_archiver.adapters.pdf.render_pdf import render_pdf
from zammad_pdf_archiver.adapters.signing.sign_pdf import sign_pdf
from zammad_pdf_archiver.adapters.snapshot.build_snapshot import build_snapshot
from zammad_pdf_archiver.domain.snapshot_models import Snapshot
from zammad_pdf_archiver.observability.metrics import render_seconds, sign_seconds

if TYPE_CHECKING:
    from zammad_pdf_archiver.adapters.zammad.client import AsyncZammadClient
    from zammad_pdf_archiver.adapters.zammad.models import TagList, Ticket
    from zammad_pdf_archiver.config.settings import Settings

log = structlog.get_logger(__name__)


def _cap_articles_if_configured(
    snapshot: Snapshot,
    *,
    ticket_id: int,
    settings: Settings,
) -> Snapshot:
    max_articles = settings.pdf.max_articles
    if (
        settings.pdf.article_limit_mode == "cap_and_continue"
        and max_articles > 0
        and len(snapshot.articles) > max_articles
    ):
        log.warning(
            "process_ticket.article_limit_capped",
            ticket_id=ticket_id,
            total=len(snapshot.articles),
            cap=max_articles,
        )
        return Snapshot(
            ticket=snapshot.ticket,
            articles=snapshot.articles[:max_articles],
        )
    return snapshot


async def build_and_render_pdf(
    *,
    client: AsyncZammadClient,
    ticket_id: int,
    ticket: Ticket,
    tags: TagList,
    settings: Settings,
) -> tuple[bytes, Snapshot]:
    snapshot = await build_snapshot(client, ticket_id, ticket=ticket, tags=tags)
    snapshot = _cap_articles_if_configured(
        snapshot,
        ticket_id=ticket_id,
        settings=settings,
    )

    render_started = perf_counter()
    pdf_bytes = await render_pdf(
        snapshot,
        locale=settings.pdf.locale,
        timezone=settings.pdf.timezone,
    )
    render_seconds.observe(perf_counter() - render_started)

    if settings.signing.enabled:
        sign_started = perf_counter()
        pdf_bytes = sign_pdf(pdf_bytes, signing=settings.signing)
        sign_seconds.observe(perf_counter() - sign_started)

    return pdf_bytes, snapshot
