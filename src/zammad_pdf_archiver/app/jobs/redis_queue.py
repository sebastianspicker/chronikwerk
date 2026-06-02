"""Redis-backed job queue: worker lifecycle, message processing, public API, and DLQ.

Internal helpers for pure data-model and stream I/O live in focused submodules:
- _queue_types.py    -- envelope dataclass and primitive parsers
- _queue_stream.py   -- stream I/O helpers that accept an injected redis client
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import time
from dataclasses import dataclass
from typing import Any

import structlog

from zammad_pdf_archiver.adapters.redis_pool import get_redis
from zammad_pdf_archiver.app.jobs import _queue_admin
from zammad_pdf_archiver.app.jobs._queue_stream import (
    _ack_and_delete,
    _claim_stale_pending,
    _ensure_group,
    _push_dlq,
    _read_new_messages,
    _read_own_pending,
)
from zammad_pdf_archiver.app.jobs._queue_types import (
    _as_str,
    _decode_envelope,
    _QueueEnvelope,
)
from zammad_pdf_archiver.app.jobs.history import record_history_event
from zammad_pdf_archiver.app.jobs.process_ticket import ProcessTicketStatus, process_ticket
from zammad_pdf_archiver.config.settings import Settings
from zammad_pdf_archiver.domain.exc_format import bounded_exc_message
from zammad_pdf_archiver.observability.metrics import (
    history_record_failed_total,
    queue_enqueued_total,
    queue_failed_total,
    queue_partial_total,
    queue_pending_count,
    queue_processed_total,
    queue_retried_total,
    queue_skipped_total,
    queue_unknown_status_total,
)

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class _HandledTicketResult:
    status: ProcessTicketStatus
    message: str | None

# ---------------------------------------------------------------------------
# Redis connection helper
# ---------------------------------------------------------------------------


async def _get_redis(settings: Settings) -> Any:
    redis_url = settings.workflow.redis_url
    if not redis_url or not redis_url.strip():
        raise RuntimeError("workflow.redis_url is required for redis queue backend")
    return await get_redis(redis_url)


# ---------------------------------------------------------------------------
# Public enqueue API
# ---------------------------------------------------------------------------


async def enqueue_ticket_job(
    *,
    delivery_id: str | None,
    payload: dict[str, Any],
    settings: Settings,
    attempt: int = 0,
    not_before_ts: float = 0.0,
    last_error: str | None = None,
) -> str:
    """Append a new job envelope to the Redis work stream and return its message ID."""
    redis = await _get_redis(settings)
    payload_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    fields: dict[str, str] = {
        "payload_json": payload_json,
        "delivery_id": delivery_id or "",
        "attempt": str(max(0, int(attempt))),
        "not_before_ts": str(max(0.0, float(not_before_ts))),
        "enqueued_at": str(time.time()),
    }
    if last_error:
        fields["last_error"] = bounded_exc_message(last_error)
    message_id = await redis.xadd(settings.workflow.queue_stream, fields)
    queue_enqueued_total.inc()
    return _as_str(message_id)


# ---------------------------------------------------------------------------
# Per-message processing: retry scheduling and dispatch
# ---------------------------------------------------------------------------


def _retry_delay_seconds(settings: Settings, *, attempt: int) -> float:
    base = float(settings.workflow.queue_retry_backoff_seconds)
    return min(base * (2 ** max(0, attempt)), 3600)  # cap at 1 hour


async def _ack_envelope(redis: Any, *, settings: Settings, envelope: _QueueEnvelope) -> None:
    await _ack_and_delete(
        redis,
        stream=settings.workflow.queue_stream,
        group=settings.workflow.queue_group,
        message_id=envelope.message_id,
    )


async def _push_dlq_and_ack(
    redis: Any,
    *,
    settings: Settings,
    envelope: _QueueEnvelope,
    reason: str,
    error_message: str | None,
) -> None:
    await _push_dlq(
        redis,
        settings=settings,
        envelope=envelope,
        reason=reason,
        error_message=error_message,
    )
    await _ack_envelope(redis, settings=settings, envelope=envelope)
    queue_failed_total.inc()


async def _handle_failed_transient_status(
    redis: Any,
    *,
    settings: Settings,
    envelope: _QueueEnvelope,
    message: str,
) -> None:
    if envelope.attempt < settings.workflow.queue_retry_max_attempts:
        delay = _retry_delay_seconds(settings, attempt=envelope.attempt)
        await enqueue_ticket_job(
            delivery_id=envelope.delivery_id,
            payload=envelope.payload,
            settings=settings,
            attempt=envelope.attempt + 1,
            not_before_ts=time.time() + delay,
            last_error=message or envelope.last_error,
        )
        await _ack_envelope(redis, settings=settings, envelope=envelope)
        queue_retried_total.inc()
        return

    await _push_dlq_and_ack(
        redis,
        settings=settings,
        envelope=envelope,
        reason="retry_exhausted",
        error_message=message or envelope.last_error,
    )


async def _ack_known_nonfailed_status(
    redis: Any, *, settings: Settings, envelope: _QueueEnvelope, status: ProcessTicketStatus
) -> bool:
    if status == "processed":
        queue_processed_total.inc()
        await _ack_envelope(redis, settings=settings, envelope=envelope)
        return True
    if status in {"processed_done_update_failed", "processed_acknowledgement_failed"}:
        queue_partial_total.inc()
        await _ack_envelope(redis, settings=settings, envelope=envelope)
        return True
    if status == "skipped_no_ticket_id":
        queue_skipped_total.labels(reason="no_ticket_id").inc()
        await _ack_envelope(redis, settings=settings, envelope=envelope)
        return True
    if status == "skipped_not_triggered":
        queue_skipped_total.labels(reason="not_triggered").inc()
        await _ack_envelope(redis, settings=settings, envelope=envelope)
        return True
    if status == "skipped_in_flight":
        queue_skipped_total.labels(reason="in_flight").inc()
        await _ack_envelope(redis, settings=settings, envelope=envelope)
        return True
    if status == "skipped_idempotency":
        queue_skipped_total.labels(reason="idempotency").inc()
        await _ack_envelope(redis, settings=settings, envelope=envelope)
        return True
    return False


async def _handle_envelope(redis: Any, *, settings: Settings, envelope: _QueueEnvelope) -> float:
    """Process one envelope: respect not-before delay, dispatch, retry or DLQ on failure.

    Returns remaining delay in seconds (>0 means the message should be revisited later).
    """
    now = time.time()
    if envelope.not_before_ts > now:
        return envelope.not_before_ts - now

    result = await _run_ticket_for_envelope(envelope, settings)
    await _route_envelope_result(redis, settings=settings, envelope=envelope, result=result)
    return 0.0


async def _run_ticket_for_envelope(
    envelope: _QueueEnvelope,
    settings: Settings,
) -> _HandledTicketResult:
    try:
        result = await process_ticket(envelope.delivery_id, envelope.payload, settings)
    except Exception as exc:  # pragma: no cover - defensive fallback
        return _HandledTicketResult(
            status="failed_transient",
            message=f"{exc.__class__.__name__}: {exc}",
        )

    _record_process_ticket_side_effects(result, envelope)
    return _HandledTicketResult(status=result.status, message=result.message)


def _record_process_ticket_side_effects(result: Any, envelope: _QueueEnvelope) -> None:
    if result.history_recorded is False:
        history_record_failed_total.inc()
        log.warning(
            "process_ticket.history_not_recorded",
            ticket_id=result.ticket_id,
            message_id=envelope.message_id,
            delivery_id=envelope.delivery_id,
        )
    if result.lock_release_failed:
        log.warning(
            "queue.worker.ticket_lock_release_failed",
            ticket_id=result.ticket_id,
            message_id=envelope.message_id,
            delivery_id=envelope.delivery_id,
        )


async def _route_envelope_result(
    redis: Any,
    *,
    settings: Settings,
    envelope: _QueueEnvelope,
    result: _HandledTicketResult,
) -> None:
    status = result.status
    message = result.message
    if status == "failed_transient":
        await _handle_failed_transient_status(
            redis,
            settings=settings,
            envelope=envelope,
            message=message or envelope.last_error or "",
        )
    elif status == "failed_permanent":
        await _push_dlq_and_ack(
            redis,
            settings=settings,
            envelope=envelope,
            reason="permanent_error",
            error_message=message or envelope.last_error,
        )
    elif not await _ack_known_nonfailed_status(
        redis, settings=settings, envelope=envelope, status=status
    ):
        error_message = message or f"unknown process_ticket status: {status}"
        queue_unknown_status_total.inc()
        log.error(
            "queue.worker.unknown_process_ticket_status",
            status=status,
            message_id=envelope.message_id,
            attempt=envelope.attempt,
        )
        await _push_dlq_and_ack(
            redis,
            settings=settings,
            envelope=envelope,
            reason="unknown_status",
            error_message=error_message,
        )


async def _process_messages(
    redis: Any,
    *,
    settings: Settings,
    messages: list[tuple[Any, Any]],
) -> float | None:
    """Decode and handle a batch of stream messages; returns smallest pending delay if any."""
    min_delay: float | None = None

    for message_id, raw_fields in messages:
        try:
            envelope = _decode_envelope(message_id, raw_fields)
        except Exception as exc:
            await _handle_invalid_queue_message(redis, settings, message_id=message_id, exc=exc)
            continue

        try:
            delay = await _handle_envelope(redis, settings=settings, envelope=envelope)
            if delay is not None and delay > 0:
                min_delay = delay if min_delay is None else min(min_delay, delay)
        except Exception as exc:
            await _handle_queue_message_exception(
                redis,
                settings,
                envelope=envelope,
                exc=exc,
            )

    return min_delay


async def _handle_invalid_queue_message(
    redis: Any,
    settings: Settings,
    *,
    message_id: Any,
    exc: Exception,
) -> None:
    envelope = _QueueEnvelope(
        message_id=_as_str(message_id),
        payload={},
        delivery_id=None,
        attempt=0,
        not_before_ts=0.0,
        last_error=str(exc),
    )
    await _push_dlq(
        redis,
        settings=settings,
        envelope=envelope,
        reason="invalid_message",
        error_message=str(exc),
    )
    await record_history_event(
        settings,
        status="failed_permanent",
        ticket_id=None,
        classification="Permanent",
        message=f"invalid_message: {exc}",
        delivery_id=None,
        request_id=None,
    )
    await _ack_and_delete(
        redis,
        stream=settings.workflow.queue_stream,
        group=settings.workflow.queue_group,
        message_id=envelope.message_id,
    )
    queue_failed_total.inc()


async def _handle_queue_message_exception(
    redis: Any,
    settings: Settings,
    *,
    envelope: _QueueEnvelope,
    exc: Exception,
) -> None:
    log.exception(
        "queue.worker.handle_message_failed",
        message_id=envelope.message_id,
        attempt=envelope.attempt,
    )
    await _push_dlq(
        redis,
        settings=settings,
        envelope=envelope,
        reason="handler_exception",
        error_message=f"{exc.__class__.__name__}: {exc}",
    )
    await record_history_event(
        settings,
        status="failed_transient",
        ticket_id=None,
        classification="Transient",
        message=f"handler_exception: {exc}",
        delivery_id=envelope.delivery_id,
        request_id=None,
    )
    await _ack_and_delete(
        redis,
        stream=settings.workflow.queue_stream,
        group=settings.workflow.queue_group,
        message_id=envelope.message_id,
    )
    queue_failed_total.inc()


# ---------------------------------------------------------------------------
# Worker lifecycle
# ---------------------------------------------------------------------------


_worker_task: asyncio.Task[None] | None = None
_worker_stop_event: asyncio.Event | None = None
_worker_lifecycle_guard = asyncio.Lock()

# Each FastAPI process owns at most one queue worker. Redis consumer groups coordinate
# between processes; this module only tracks the process-local task and stop event.


def _backend(settings: Settings) -> str:
    return (settings.workflow.execution_backend or "inprocess").strip().lower()


def _consumer_name(settings: Settings) -> str:
    configured = settings.workflow.queue_consumer
    if configured and configured.strip():
        return configured.strip()
    return f"{socket.gethostname()}-{os.getpid()}"


async def _worker_loop(settings: Settings, stop_event: asyncio.Event) -> None:
    """Main consumer loop: claim stale, process pending, then poll for new messages."""
    redis = await _get_redis(settings)
    consumer = _consumer_name(settings)
    stream = settings.workflow.queue_stream
    group = settings.workflow.queue_group
    await _ensure_group(redis, stream=stream, group=group)

    consecutive_failures = 0

    while not stop_event.is_set():
        try:
            min_delay = await _run_worker_iteration(
                redis,
                settings=settings,
                consumer=consumer,
            )
            consecutive_failures = 0

            if min_delay is not None and min_delay > 0:
                await asyncio.sleep(min(min_delay, 1.0))
        except asyncio.CancelledError:  # pragma: no cover
            raise
        # Fail loud, probe Redis, back off, then keep the worker alive.
        except Exception:
            consecutive_failures += 1
            log.exception("queue.worker.loop_error", consecutive_failures=consecutive_failures)
            await _backoff_after_worker_error(redis, consecutive_failures)


async def _run_worker_iteration(
    redis: Any,
    *,
    settings: Settings,
    consumer: str,
) -> float | None:
    stream = settings.workflow.queue_stream
    group = settings.workflow.queue_group
    read_count = settings.workflow.queue_read_count

    claimed = await _claim_stale_pending(
        redis,
        stream=stream,
        group=group,
        consumer=consumer,
        count=read_count,
    )
    claimed_delay = await _process_messages(redis, settings=settings, messages=claimed)

    pending = await _read_own_pending(
        redis,
        stream=stream,
        group=group,
        consumer=consumer,
        count=read_count,
    )
    pending_delay = await _process_messages(redis, settings=settings, messages=pending)

    block_ms = 1 if (claimed or pending) else settings.workflow.queue_read_block_ms
    new_messages = await _read_new_messages(
        redis,
        stream=stream,
        group=group,
        consumer=consumer,
        count=read_count,
        block_ms=block_ms,
    )
    new_delay = await _process_messages(redis, settings=settings, messages=new_messages)
    return _minimum_positive_delay(claimed_delay, pending_delay, new_delay)


def _minimum_positive_delay(*delays: float | None) -> float | None:
    positive = [delay for delay in delays if delay is not None and delay > 0]
    if not positive:
        return None
    return min(positive)


async def _backoff_after_worker_error(redis: Any, consecutive_failures: int) -> None:
    try:
        await redis.ping()
    except Exception:
        log.error(
            "queue.worker.redis_unreachable",
            detail="Redis ping failed; connection may be stale.",
            consecutive_failures=consecutive_failures,
        )

    backoff = min(0.3 * (2 ** (consecutive_failures - 1)), 30.0)
    await asyncio.sleep(backoff)


async def start_queue_worker(settings: Settings) -> asyncio.Task[None] | None:
    """Start the process-local Redis worker when redis_queue is configured."""
    global _worker_stop_event, _worker_task

    if _backend(settings) != "redis_queue":
        return None

    async with _worker_lifecycle_guard:
        if _worker_task is not None and not _worker_task.done():
            return _worker_task

        _worker_stop_event = asyncio.Event()
        _worker_task = asyncio.create_task(
            _worker_loop(settings, _worker_stop_event),
            name="redis-queue-worker",
        )
        return _worker_task


async def stop_queue_worker(settings: Settings, *, timeout: float = 3.0) -> None:
    """Signal the process-local Redis worker and cancel it if graceful shutdown times out."""
    global _worker_stop_event, _worker_task

    async with _worker_lifecycle_guard:
        task = _worker_task
        stop_event = _worker_stop_event
        if task is None or stop_event is None:
            return
        stop_event.set()

    try:
        await _wait_for_worker_stop(task, timeout=timeout)
    except asyncio.CancelledError:
        if not task.cancelled():
            raise
    finally:
        async with _worker_lifecycle_guard:
            if _worker_task is task:
                _worker_task = None
                _worker_stop_event = None


async def _wait_for_worker_stop(task: asyncio.Task[None], *, timeout: float) -> None:
    done, _pending = await asyncio.wait({task}, timeout=timeout)
    if not done:
        await _cancel_timed_out_worker(task, timeout=timeout)
        return

    await _observe_stopped_worker(task, failure_event="queue.worker.stop_failed")


async def _cancel_timed_out_worker(task: asyncio.Task[None], *, timeout: float) -> None:
    log.warning("queue.worker.stop_timeout", timeout=timeout)
    task.cancel()
    await _observe_stopped_worker(task, failure_event="queue.worker.stop_failed_after_cancel")


async def _observe_stopped_worker(task: asyncio.Task[None], *, failure_event: str) -> None:
    try:
        await task
    except asyncio.CancelledError:
        pass
    except Exception:
        log.exception(failure_event)


def _pending_count(raw: Any) -> int:
    return _queue_admin.pending_count(raw)


# ---------------------------------------------------------------------------
# Public admin API
# ---------------------------------------------------------------------------


async def get_queue_stats(settings: Settings) -> dict[str, Any]:
    execution_backend = _backend(settings)
    if execution_backend != "redis_queue":
        return {
            "execution_backend": execution_backend,
            "queue_enabled": False,
        }

    return await _queue_admin.get_queue_stats(
        settings,
        get_redis_client=_get_redis,
        consumer_name=_consumer_name,
        queue_pending_count=queue_pending_count,
    )


async def drain_dlq(settings: Settings, *, limit: int = 100) -> dict[str, int]:
    return await _queue_admin.drain_dlq(settings, get_redis_client=_get_redis, limit=limit)


async def replay_dlq(settings: Settings, *, limit: int = 10) -> dict[str, int]:
    return await _queue_admin.replay_dlq(
        settings,
        get_redis_client=_get_redis,
        enqueue_ticket_job=enqueue_ticket_job,
        log=log,
        limit=limit,
    )


async def aclose_queue_clients() -> int:
    from zammad_pdf_archiver.adapters.redis_pool import close_all

    return await close_all()
