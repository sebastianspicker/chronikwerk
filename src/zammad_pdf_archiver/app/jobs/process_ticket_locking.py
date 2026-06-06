from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import Any

from zammad_pdf_archiver.app.jobs._process_ticket_models import (
    ProcessTicketResult,
    _TicketJobContext,
)
from zammad_pdf_archiver.domain.errors import TransientError

AcquireTicket = Callable[[Any, int], Awaitable[bool]]
SkipInFlight = Callable[[_TicketJobContext], Awaitable[ProcessTicketResult]]
ClaimDeliveryOrSkip = Callable[[_TicketJobContext], Awaitable[ProcessTicketResult | None]]
ProcessWithClient = Callable[[_TicketJobContext], Awaitable[ProcessTicketResult]]
ReleaseTicketLock = Callable[[_TicketJobContext], Awaitable[bool]]
HandleLockUnavailable = Callable[
    [_TicketJobContext, TransientError],
    Awaitable[ProcessTicketResult],
]


async def process_with_ticket_lock(
    ctx: _TicketJobContext,
    *,
    payload: dict[str, Any],
    acquire_ticket: AcquireTicket,
    skip_in_flight: SkipInFlight,
    claim_delivery_or_skip: ClaimDeliveryOrSkip,
    process_with_client: ProcessWithClient,
    release_ticket_lock: ReleaseTicketLock,
    handle_lock_unavailable: HandleLockUnavailable,
) -> ProcessTicketResult:
    """Acquire an exclusive lock for the ticket, then run the processing pipeline."""
    try:
        acquired = await acquire_ticket(ctx.settings, ctx.ticket_id)
    except TransientError as exc:
        return await handle_lock_unavailable(ctx, exc)
    if not acquired:
        return await skip_in_flight(ctx)

    result: ProcessTicketResult | None = None
    try:
        claimed = await claim_delivery_or_skip(ctx)
        if claimed is not None:
            result = claimed
        else:
            result = await process_with_client(ctx)
    finally:
        lock_release_failed = await release_ticket_lock(ctx)

    if result is None:  # pragma: no cover - defensive; exceptions exit through finally above.
        raise RuntimeError("process_ticket completed without a result")
    if lock_release_failed:
        return replace(result, lock_release_failed=True)
    return result
