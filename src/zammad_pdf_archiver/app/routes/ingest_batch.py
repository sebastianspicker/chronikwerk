from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from starlette.responses import JSONResponse

from zammad_pdf_archiver.app.responses import api_error
from zammad_pdf_archiver.config.settings import Settings

DispatchTicket = Callable[..., Awaitable[None]]
PublicPayloadForJob = Callable[[Any, str | None], dict[str, Any]]


def batch_item_delivery_id(batch_delivery_id: str | None, index: int) -> str | None:
    if batch_delivery_id is None:
        return None
    # One delivery header represents the batch request; suffix with the item index so
    # idempotency is still tracked per ticket payload.
    return ":".join((batch_delivery_id, str(index)))


def batch_too_large_response(max_batch_size: int) -> JSONResponse:
    return api_error(
        422,
        f"batch too large (max {max_batch_size} items)",
        code="batch_too_large",
    )


def batch_dispatch_failure_response(
    *,
    accepted: int,
    failed_index: int,
    ticket_id: int,
) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={
            "status": "partial_failure" if accepted else "failed",
            "code": "batch_dispatch_failed",
            "accepted": accepted,
            "failed_index": failed_index,
            "failed_ticket_id": ticket_id,
        },
    )


async def dispatch_batch_payloads(
    *,
    payloads: list[Any],
    settings: Settings,
    batch_delivery_id: str | None,
    request_id: str | None,
    dispatch_ticket: DispatchTicket,
    public_payload_for_job: PublicPayloadForJob,
    log,
) -> tuple[int, JSONResponse | None]:
    accepted = 0

    for index, payload in enumerate(payloads):
        ticket_id = payload.resolved_ticket_id()
        if ticket_id is None:
            continue

        try:
            await dispatch_ticket(
                delivery_id=batch_item_delivery_id(batch_delivery_id, index),
                payload_for_job=public_payload_for_job(payload, request_id),
                settings=settings,
            )
        except Exception:
            log.exception(
                "ingest.batch_dispatch_failed",
                accepted=accepted,
                failed_index=index,
                ticket_id=ticket_id,
            )
            return accepted, batch_dispatch_failure_response(
                accepted=accepted,
                failed_index=index,
                ticket_id=ticket_id,
            )
        accepted += 1

    return accepted, None
