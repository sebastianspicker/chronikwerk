"""Coordinate one ticket archival pipeline from fetch through storage."""

from __future__ import annotations

import asyncio
from time import perf_counter
from typing import Any

import structlog

from chronikwerk.adapters.zammad.client import AsyncZammadClient
from chronikwerk.app.constants import FORCE_REPROCESS_KEY, REQUEST_ID_KEY
from chronikwerk.app.jobs._ticket_pipeline import (
    ProcessTicketResult,
    TicketJobContext,
)
from chronikwerk.app.jobs._ticket_pipeline import (
    cleanup_cancelled_pipeline as _cleanup_cancelled_pipeline,
)
from chronikwerk.app.jobs._ticket_pipeline import (
    record_history as _record_history,
)
from chronikwerk.app.jobs._ticket_pipeline import (
    run_ticket_pipeline as _run_ticket_pipeline,
)
from chronikwerk.app.jobs._ticket_pipeline_errors import (
    handle_ticket_pipeline_exception as _handle_ticket_pipeline_exception,
)
from chronikwerk.app.jobs.ticket_stores import (
    release_ticket,
    try_acquire_ticket,
    try_claim_delivery_id,
)
from chronikwerk.config.settings import Settings
from chronikwerk.domain.state_machine import TRIGGER_TAG
from chronikwerk.domain.ticket_id import extract_ticket_id
from chronikwerk.observability.metrics import (
    skipped_total,
    total_seconds,
)

log = structlog.get_logger(__name__)

# Compatibility alias for focused tests and callers that inspect private job state.
_TicketJobContext = TicketJobContext


async def process_ticket(
    delivery_id: str | None,
    payload: dict[str, Any],
    settings: Settings,
) -> ProcessTicketResult:
    """Orchestrate the full ticket archival pipeline for a single ingest payload."""
    raw_request_id = payload.get(REQUEST_ID_KEY)
    ticket_id = extract_ticket_id(payload)
    if ticket_id is None:
        request_id = raw_request_id if isinstance(raw_request_id, str) else None
        log.info("process_ticket.skip_no_ticket_id", request_id=request_id)
        skipped_total.labels(reason="no_ticket_id").inc()
        stub_ctx = TicketJobContext(
            settings=settings,
            ticket_id=0,
            delivery_id=delivery_id,
            request_id=request_id,
        )
        _record_history(stub_ctx, status="skipped_no_ticket_id")
        return ProcessTicketResult(status="skipped_no_ticket_id", ticket_id=None)

    request_id = (
        raw_request_id if isinstance(raw_request_id, str) and raw_request_id.strip() else None
    )
    ctx = TicketJobContext(
        settings=settings,
        ticket_id=ticket_id,
        delivery_id=delivery_id,
        request_id=request_id,
    )
    _record_history(ctx, status="running")

    with structlog.contextvars.bound_contextvars(**_bound_context(ctx)):
        return await _process_with_ticket_lock(ctx, payload=payload)


def _bound_context(ctx: TicketJobContext) -> dict[str, object]:
    bound: dict[str, object] = {"ticket_id": ctx.ticket_id}
    if ctx.delivery_id:
        bound["delivery_id"] = ctx.delivery_id
    if ctx.request_id:
        bound["request_id"] = ctx.request_id
    return bound


def _force_reprocess_requested(payload: dict[str, Any]) -> bool:
    return payload.get(FORCE_REPROCESS_KEY) is True


async def _process_with_ticket_lock(
    ctx: TicketJobContext,
    *,
    payload: dict[str, Any],
) -> ProcessTicketResult:
    acquired = await try_acquire_ticket(ctx.settings, ctx.ticket_id)
    if not acquired:
        return await _skip_in_flight(ctx)

    try:
        claimed = await _claim_delivery_or_skip(ctx)
        if claimed is not None:
            return claimed
        return await _process_ticket_with_client(ctx, payload=payload)
    finally:
        await _release_ticket_lock(ctx)


async def _skip_in_flight(ctx: TicketJobContext) -> ProcessTicketResult:
    """Return a skip result when another worker is already processing this ticket."""
    log.info(
        "process_ticket.skip_ticket_in_flight",
        ticket_id=ctx.ticket_id,
        delivery_id=ctx.delivery_id,
    )
    skipped_total.labels(reason="in_flight").inc()
    _record_history(ctx, status="skipped_in_flight")
    return ProcessTicketResult(status="skipped_in_flight", ticket_id=ctx.ticket_id)


async def _claim_delivery_or_skip(ctx: TicketJobContext) -> ProcessTicketResult | None:
    """Enforce at-most-once delivery; return a skip result for a claimed delivery."""
    if not ctx.delivery_id:
        return None
    if await try_claim_delivery_id(ctx.settings, ctx.delivery_id):
        return None

    log.info(
        "process_ticket.skip_delivery_id_seen",
        ticket_id=ctx.ticket_id,
        delivery_id=ctx.delivery_id,
    )
    skipped_total.labels(reason="idempotency").inc()
    _record_history(ctx, status="skipped_idempotency")
    return ProcessTicketResult(status="skipped_idempotency", ticket_id=ctx.ticket_id)


async def _process_ticket_with_client(
    ctx: TicketJobContext,
    *,
    payload: dict[str, Any],
) -> ProcessTicketResult:
    """Open a Zammad client session and preserve the job-level error boundary."""
    settings = ctx.settings
    trigger_tag = str(settings.workflow.trigger_tag).strip() or TRIGGER_TAG
    require_trigger_tag = bool(settings.workflow.require_tag)
    force_reprocess = _force_reprocess_requested(payload)

    async with AsyncZammadClient(connection=settings.zammad_connection) as client:
        observe_total = True
        total_start = perf_counter()
        try:
            result, observe_total = await _run_ticket_pipeline(
                client=client,
                ctx=ctx,
                payload=payload,
                trigger_tag=trigger_tag,
                require_trigger_tag=require_trigger_tag,
                force_reprocess=force_reprocess,
            )
            return result
        except Exception as exc:  # pylint: disable=broad-exception-caught
            try:
                return await _handle_ticket_pipeline_exception(
                    client=client,
                    ctx=ctx,
                    trigger_tag=trigger_tag,
                    exc=exc,
                )
            except asyncio.CancelledError:
                await _cleanup_cancelled_pipeline(
                    client=client,
                    ctx=ctx,
                    trigger_tag=trigger_tag,
                )
                raise
        finally:
            if observe_total:
                total_seconds.observe(perf_counter() - total_start)


async def _release_ticket_lock(ctx: TicketJobContext) -> None:
    try:
        await asyncio.shield(release_ticket(ctx.ticket_id))
    except Exception:  # pylint: disable=broad-exception-caught
        log.exception(
            "process_ticket.release_ticket_failed",
            ticket_id=ctx.ticket_id,
            request_id=ctx.request_id,
            delivery_id=ctx.delivery_id,
        )
