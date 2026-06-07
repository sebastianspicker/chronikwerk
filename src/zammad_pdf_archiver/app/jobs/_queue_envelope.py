from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from zammad_pdf_archiver.app.jobs._queue_types import _QueueEnvelope
from zammad_pdf_archiver.app.jobs.process_ticket import ProcessTicketStatus
from zammad_pdf_archiver.config.settings import Settings


@dataclass(frozen=True)
class QueueEnvelopeDeps:
    process_ticket: Callable[[str | None, dict[str, Any], Settings], Awaitable[Any]]
    enqueue_ticket_job: Callable[..., Awaitable[str]]
    ack_envelope: Callable[..., Awaitable[None]]
    push_dlq_and_ack: Callable[..., Awaitable[None]]
    history_record_failed_total: Any
    queue_partial_total: Any
    queue_processed_total: Any
    queue_retried_total: Any
    queue_skipped_total: Any
    queue_unknown_status_total: Any
    log: Any
    time_fn: Callable[[], float] = time.time


@dataclass(frozen=True)
class HandledTicketResult:
    status: ProcessTicketStatus
    message: str | None


def retry_delay_seconds(settings: Settings, *, attempt: int) -> float:
    base = float(settings.workflow.queue_retry_backoff_seconds)
    return min(base * (2 ** max(0, attempt)), 3600)  # cap at 1 hour


async def handle_failed_transient_status(
    redis: Any,
    *,
    settings: Settings,
    envelope: _QueueEnvelope,
    message: str,
    deps: QueueEnvelopeDeps,
) -> None:
    if envelope.attempt < settings.workflow.queue_retry_max_attempts:
        delay = retry_delay_seconds(settings, attempt=envelope.attempt)
        await deps.enqueue_ticket_job(
            delivery_id=envelope.delivery_id,
            payload=envelope.payload,
            settings=settings,
            attempt=envelope.attempt + 1,
            not_before_ts=deps.time_fn() + delay,
            last_error=message or envelope.last_error,
        )
        await deps.ack_envelope(redis, settings=settings, envelope=envelope)
        deps.queue_retried_total.inc()
        return

    await deps.push_dlq_and_ack(
        redis,
        settings=settings,
        envelope=envelope,
        reason="retry_exhausted",
        error_message=message or envelope.last_error,
    )


async def ack_known_nonfailed_status(
    redis: Any,
    *,
    settings: Settings,
    envelope: _QueueEnvelope,
    status: ProcessTicketStatus,
    deps: QueueEnvelopeDeps,
) -> bool:
    if status == "processed":
        deps.queue_processed_total.inc()
        await deps.ack_envelope(redis, settings=settings, envelope=envelope)
        return True
    if status in {"processed_done_update_failed", "processed_acknowledgement_failed"}:
        deps.queue_partial_total.inc()
        await deps.ack_envelope(redis, settings=settings, envelope=envelope)
        return True
    if status == "skipped_no_ticket_id":
        deps.queue_skipped_total.labels(reason="no_ticket_id").inc()
        await deps.ack_envelope(redis, settings=settings, envelope=envelope)
        return True
    if status == "skipped_not_triggered":
        deps.queue_skipped_total.labels(reason="not_triggered").inc()
        await deps.ack_envelope(redis, settings=settings, envelope=envelope)
        return True
    if status == "skipped_in_flight":
        deps.queue_skipped_total.labels(reason="in_flight").inc()
        await deps.ack_envelope(redis, settings=settings, envelope=envelope)
        return True
    if status == "skipped_idempotency":
        deps.queue_skipped_total.labels(reason="idempotency").inc()
        await deps.ack_envelope(redis, settings=settings, envelope=envelope)
        return True
    return False


async def handle_envelope(
    redis: Any,
    *,
    settings: Settings,
    envelope: _QueueEnvelope,
    deps: QueueEnvelopeDeps,
) -> float:
    """Process one envelope: respect not-before delay, dispatch, retry or DLQ on failure."""
    now = deps.time_fn()
    if envelope.not_before_ts > now:
        return envelope.not_before_ts - now

    result = await run_ticket_for_envelope(envelope, settings, deps=deps)
    await route_envelope_result(
        redis,
        settings=settings,
        envelope=envelope,
        result=result,
        deps=deps,
    )
    return 0.0


async def run_ticket_for_envelope(
    envelope: _QueueEnvelope,
    settings: Settings,
    *,
    deps: QueueEnvelopeDeps,
) -> HandledTicketResult:
    try:
        result = await deps.process_ticket(envelope.delivery_id, envelope.payload, settings)
    except Exception as exc:  # pragma: no cover - defensive fallback
        return HandledTicketResult(
            status="failed_transient",
            message=f"{exc.__class__.__name__}: {exc}",
        )

    record_process_ticket_side_effects(result, envelope, deps=deps)
    return HandledTicketResult(status=result.status, message=result.message)


def record_process_ticket_side_effects(
    result: Any,
    envelope: _QueueEnvelope,
    *,
    deps: QueueEnvelopeDeps,
) -> None:
    if result.history_recorded is False:
        deps.history_record_failed_total.inc()
        deps.log.warning(
            "process_ticket.history_not_recorded",
            ticket_id=result.ticket_id,
            message_id=envelope.message_id,
            delivery_id=envelope.delivery_id,
        )
    if result.lock_release_failed:
        deps.log.warning(
            "queue.worker.ticket_lock_release_failed",
            ticket_id=result.ticket_id,
            message_id=envelope.message_id,
            delivery_id=envelope.delivery_id,
        )


async def route_envelope_result(
    redis: Any,
    *,
    settings: Settings,
    envelope: _QueueEnvelope,
    result: HandledTicketResult,
    deps: QueueEnvelopeDeps,
) -> None:
    status = result.status
    message = result.message
    if status == "failed_transient":
        await handle_failed_transient_status(
            redis,
            settings=settings,
            envelope=envelope,
            message=message or envelope.last_error or "",
            deps=deps,
        )
    elif status == "failed_permanent":
        await deps.push_dlq_and_ack(
            redis,
            settings=settings,
            envelope=envelope,
            reason="permanent_error",
            error_message=message or envelope.last_error,
        )
    elif not await ack_known_nonfailed_status(
        redis, settings=settings, envelope=envelope, status=status, deps=deps
    ):
        error_message = message or f"unknown process_ticket status: {status}"
        deps.queue_unknown_status_total.inc()
        deps.log.error(
            "queue.worker.unknown_process_ticket_status",
            status=status,
            message_id=envelope.message_id,
            attempt=envelope.attempt,
        )
        await deps.push_dlq_and_ack(
            redis,
            settings=settings,
            envelope=envelope,
            reason="unknown_status",
            error_message=error_message,
        )
