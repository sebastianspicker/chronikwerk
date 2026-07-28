"""Build normalized snapshots from Zammad tickets and render them as PDFs."""

from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter
from typing import TYPE_CHECKING

import structlog

from chronikwerk.adapters.pdf.render_pdf import render_pdf
from chronikwerk.adapters.signing.sign_pdf import sign_pdf_with_provenance
from chronikwerk.adapters.snapshot.build_snapshot import build_snapshot
from chronikwerk.domain.async_work import run_sync_cancellation_safe
from chronikwerk.domain.snapshot_models import Snapshot
from chronikwerk.observability.metrics import render_seconds, sign_seconds

if TYPE_CHECKING:
    from chronikwerk.adapters.zammad.client import AsyncZammadClient
    from chronikwerk.adapters.zammad.models import TagList, Ticket
    from chronikwerk.config.settings import Settings

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class RenderedTicket:
    """Bundle rendered bytes with the normalized snapshot and signing provenance."""

    pdf_bytes: bytes
    snapshot: Snapshot
    signing_cert_fingerprint: str | None


def _cap_articles_if_configured(
    snapshot: Snapshot,
    *,
    ticket_id: int,
    settings: Settings,
) -> Snapshot:
    max_articles = settings.pdf.max_articles
    if settings.pdf.article_limit_mode == "cap_and_continue" and 0 < max_articles < len(
        snapshot.articles
    ):
        log.warning(
            "process_ticket.article_limit_capped",
            ticket_id=ticket_id,
            total=len(snapshot.articles),
            cap=max_articles,
        )
        total = snapshot.articles_total
        if total is None:
            total = len(snapshot.articles)
        return snapshot.model_copy(
            update={
                "articles": snapshot.articles[:max_articles],
                "articles_total": total,
                "articles_omitted": total - max_articles,
            }
        )
    return snapshot


async def build_and_render_pdf(
    *,
    client: AsyncZammadClient,
    ticket_id: int,
    ticket: Ticket,
    tags: TagList,
    settings: Settings,
) -> RenderedTicket:
    """Fetch render inputs and produce PDF bytes for one ticket."""
    snapshot = await build_snapshot(client, ticket_id, ticket=ticket, tags=tags)
    snapshot = _cap_articles_if_configured(
        snapshot,
        ticket_id=ticket_id,
        settings=settings,
    )

    render_started = perf_counter()
    pdf_bytes = await render_pdf(
        snapshot,
        max_articles=settings.pdf.max_articles,
        locale=settings.pdf.locale,
        timezone=settings.pdf.timezone,
    )
    render_seconds.observe(perf_counter() - render_started)

    signing_cert_fingerprint = None
    if settings.signing.enabled:
        sign_started = perf_counter()
        signed_pdf = await run_sync_cancellation_safe(
            sign_pdf_with_provenance,
            pdf_bytes,
            signing=settings.signing,
            trust_env=settings.hardening.transport.trust_env,
            allow_insecure_http=settings.hardening.transport.allow_insecure_http,
            allow_private_networks=settings.hardening.transport.allow_private_networks,
        )
        pdf_bytes = signed_pdf.pdf_bytes
        signing_cert_fingerprint = signed_pdf.certificate_fingerprint
        sign_seconds.observe(perf_counter() - sign_started)

    return RenderedTicket(
        pdf_bytes=pdf_bytes,
        snapshot=snapshot,
        signing_cert_fingerprint=signing_cert_fingerprint,
    )
