from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from zammad_pdf_archiver.app.jobs._queue_types import _as_str, _decode_envelope, _QueueEnvelope
from zammad_pdf_archiver.config.settings import Settings


@dataclass(frozen=True)
class QueueProcessingDeps:
    handle_envelope: Callable[..., Awaitable[float]]
    push_dlq: Callable[..., Awaitable[None]]
    ack_and_delete: Callable[..., Awaitable[None]]
    record_history_event: Callable[..., Awaitable[bool]]
    queue_failed_total: Any
    log: Any


async def process_messages(
    redis: Any,
    *,
    settings: Settings,
    messages: list[tuple[Any, Any]],
    deps: QueueProcessingDeps,
) -> float | None:
    """Decode and handle a batch of stream messages; returns smallest pending delay if any."""
    min_delay: float | None = None

    for message_id, raw_fields in messages:
        try:
            envelope = _decode_envelope(message_id, raw_fields)
        except Exception as exc:
            await handle_invalid_queue_message(
                redis, settings, message_id=message_id, exc=exc, deps=deps
            )
            continue

        try:
            delay = await deps.handle_envelope(redis, settings=settings, envelope=envelope)
            if delay is not None and delay > 0:
                min_delay = delay if min_delay is None else min(min_delay, delay)
        except Exception as exc:
            await handle_queue_message_exception(
                redis,
                settings,
                envelope=envelope,
                exc=exc,
                deps=deps,
            )

    return min_delay


async def handle_invalid_queue_message(
    redis: Any,
    settings: Settings,
    *,
    message_id: Any,
    exc: Exception,
    deps: QueueProcessingDeps,
) -> None:
    envelope = _QueueEnvelope(
        message_id=_as_str(message_id),
        payload={},
        delivery_id=None,
        attempt=0,
        not_before_ts=0.0,
        last_error=str(exc),
    )
    await deps.push_dlq(
        redis,
        settings=settings,
        envelope=envelope,
        reason="invalid_message",
        error_message=str(exc),
    )
    await deps.record_history_event(
        settings,
        status="failed_permanent",
        ticket_id=None,
        classification="Permanent",
        message=f"invalid_message: {exc}",
        delivery_id=None,
        request_id=None,
    )
    await deps.ack_and_delete(
        redis,
        stream=settings.workflow.queue_stream,
        group=settings.workflow.queue_group,
        message_id=envelope.message_id,
    )
    deps.queue_failed_total.inc()


async def handle_queue_message_exception(
    redis: Any,
    settings: Settings,
    *,
    envelope: _QueueEnvelope,
    exc: Exception,
    deps: QueueProcessingDeps,
) -> None:
    deps.log.exception(
        "queue.worker.handle_message_failed",
        message_id=envelope.message_id,
        attempt=envelope.attempt,
    )
    await deps.push_dlq(
        redis,
        settings=settings,
        envelope=envelope,
        reason="handler_exception",
        error_message=f"{exc.__class__.__name__}: {exc}",
    )
    await deps.record_history_event(
        settings,
        status="failed_transient",
        ticket_id=None,
        classification="Transient",
        message=f"handler_exception: {exc}",
        delivery_id=envelope.delivery_id,
        request_id=None,
    )
    await deps.ack_and_delete(
        redis,
        stream=settings.workflow.queue_stream,
        group=settings.workflow.queue_group,
        message_id=envelope.message_id,
    )
    deps.queue_failed_total.inc()
