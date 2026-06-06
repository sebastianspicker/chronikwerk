"""Redis-backed job queue: worker lifecycle, message processing, public API, and DLQ.

Internal helpers for pure data-model and stream I/O live in focused submodules:
- _queue_types.py    -- envelope dataclass and primitive parsers
- _queue_stream.py   -- stream I/O helpers that accept an injected redis client
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import structlog

from zammad_pdf_archiver.adapters.redis_pool import get_redis
from zammad_pdf_archiver.app.jobs import _queue_admin
from zammad_pdf_archiver.app.jobs._queue_envelope import QueueEnvelopeDeps
from zammad_pdf_archiver.app.jobs._queue_envelope import (
    handle_envelope as _handle_envelope_impl,
)
from zammad_pdf_archiver.app.jobs._queue_envelope import (
    retry_delay_seconds as _retry_delay_seconds_impl,
)
from zammad_pdf_archiver.app.jobs._queue_lifecycle import WorkerLoopDeps
from zammad_pdf_archiver.app.jobs._queue_lifecycle import backend as _backend
from zammad_pdf_archiver.app.jobs._queue_lifecycle import consumer_name as _consumer_name
from zammad_pdf_archiver.app.jobs._queue_lifecycle import (
    wait_for_worker_stop as _wait_for_worker_stop_impl,
)
from zammad_pdf_archiver.app.jobs._queue_lifecycle import (
    worker_loop as _worker_loop_impl,
)
from zammad_pdf_archiver.app.jobs._queue_processing import QueueProcessingDeps
from zammad_pdf_archiver.app.jobs._queue_processing import (
    process_messages as _process_messages_impl,
)
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
    _QueueEnvelope,
)
from zammad_pdf_archiver.app.jobs.history import record_history_event
from zammad_pdf_archiver.app.jobs.process_ticket import process_ticket
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
    return _retry_delay_seconds_impl(settings, attempt=attempt)


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


def _envelope_deps() -> QueueEnvelopeDeps:
    return QueueEnvelopeDeps(
        process_ticket=process_ticket,
        enqueue_ticket_job=enqueue_ticket_job,
        ack_envelope=_ack_envelope,
        push_dlq_and_ack=_push_dlq_and_ack,
        history_record_failed_total=history_record_failed_total,
        queue_partial_total=queue_partial_total,
        queue_processed_total=queue_processed_total,
        queue_retried_total=queue_retried_total,
        queue_skipped_total=queue_skipped_total,
        queue_unknown_status_total=queue_unknown_status_total,
        log=log,
        time_fn=time.time,
    )


async def _handle_envelope(redis: Any, *, settings: Settings, envelope: _QueueEnvelope) -> float:
    """Process one envelope and return a positive delay when it should be revisited later."""
    return await _handle_envelope_impl(
        redis,
        settings=settings,
        envelope=envelope,
        deps=_envelope_deps(),
    )


async def _process_messages(
    redis: Any,
    *,
    settings: Settings,
    messages: list[tuple[Any, Any]],
) -> float | None:
    return await _process_messages_impl(
        redis,
        settings=settings,
        messages=messages,
        deps=QueueProcessingDeps(
            handle_envelope=_handle_envelope,
            push_dlq=_push_dlq,
            ack_and_delete=_ack_and_delete,
            record_history_event=record_history_event,
            queue_failed_total=queue_failed_total,
            log=log,
        ),
    )


# ---------------------------------------------------------------------------
# Worker lifecycle
# ---------------------------------------------------------------------------


_worker_task: asyncio.Task[None] | None = None
_worker_stop_event: asyncio.Event | None = None
_worker_lifecycle_guard = asyncio.Lock()

# Each FastAPI process owns at most one queue worker. Redis consumer groups coordinate
# between processes; this module only tracks the process-local task and stop event.


def _worker_loop_deps() -> WorkerLoopDeps:
    return WorkerLoopDeps(
        get_redis=_get_redis,
        ensure_group=_ensure_group,
        claim_stale_pending=_claim_stale_pending,
        read_own_pending=_read_own_pending,
        read_new_messages=_read_new_messages,
        process_messages=_process_messages,
        sleep=asyncio.sleep,
        log=log,
    )


async def _worker_loop(settings: Settings, stop_event: asyncio.Event) -> None:
    await _worker_loop_impl(settings, stop_event, deps=_worker_loop_deps())


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
        await _wait_for_worker_stop_impl(task, timeout=timeout, log=log)
    except asyncio.CancelledError:
        if not task.cancelled():
            raise
    finally:
        async with _worker_lifecycle_guard:
            if _worker_task is task:
                _worker_task = None
                _worker_stop_event = None

_pending_count = _queue_admin.pending_count


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
