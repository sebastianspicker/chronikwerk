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
from typing import Any

import structlog

from zammad_pdf_archiver.adapters.redis_pool import get_redis
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
    _merge_min_delay,
    _QueueEnvelope,
)
from zammad_pdf_archiver.app.jobs.history import record_history_event
from zammad_pdf_archiver.app.jobs.process_ticket import process_ticket
from zammad_pdf_archiver.config.settings import Settings
from zammad_pdf_archiver.observability.metrics import (
    queue_enqueued_total,
    queue_failed_total,
    queue_pending_count,
    queue_processed_total,
    queue_retried_total,
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
        fields["last_error"] = last_error[:500]
        if len(last_error) > 500:
            fields["last_error_truncated"] = "1"
    message_id = await redis.xadd(settings.workflow.queue_stream, fields)
    queue_enqueued_total.inc()
    return _as_str(message_id)


# ---------------------------------------------------------------------------
# Per-message processing: retry scheduling and dispatch
# ---------------------------------------------------------------------------


def _retry_delay_seconds(settings: Settings, *, attempt: int) -> float:
    base = float(settings.workflow.queue_retry_backoff_seconds)
    return min(base * (2 ** max(0, attempt)), 3600)  # cap at 1 hour


async def _handle_envelope(redis: Any, *, settings: Settings, envelope: _QueueEnvelope) -> float:
    """Process one envelope: respect not-before delay, dispatch, retry or DLQ on failure.

    Returns remaining delay in seconds (>0 means the message should be revisited later).
    """
    stream = settings.workflow.queue_stream
    group = settings.workflow.queue_group

    now = time.time()
    if envelope.not_before_ts > now:
        return envelope.not_before_ts - now

    try:
        result = await process_ticket(envelope.delivery_id, envelope.payload, settings)
        status = result.status
        message = result.message
    except Exception as exc:  # pragma: no cover - defensive fallback
        queue_failed_total.inc()
        status = "failed_transient"
        message = f"{exc.__class__.__name__}: {exc}"

    if status == "failed_transient":
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
            await _ack_and_delete(redis, stream=stream, group=group, message_id=envelope.message_id)
            queue_retried_total.inc()
        else:
            await _push_dlq(
                redis,
                settings=settings,
                envelope=envelope,
                reason="retry_exhausted",
                error_message=message or envelope.last_error,
            )
            await _ack_and_delete(redis, stream=stream, group=group, message_id=envelope.message_id)
    elif status == "failed_permanent":
        await _push_dlq(
            redis,
            settings=settings,
            envelope=envelope,
            reason="permanent_error",
            error_message=message or envelope.last_error,
        )
        await _ack_and_delete(redis, stream=stream, group=group, message_id=envelope.message_id)
    else:
        queue_processed_total.inc()
        await _ack_and_delete(redis, stream=stream, group=group, message_id=envelope.message_id)

    return 0.0


async def _process_messages(
    redis: Any,
    *,
    settings: Settings,
    messages: list[tuple[Any, Any]],
) -> float | None:
    """Decode and handle a batch of stream messages; returns smallest pending delay if any."""
    stream = settings.workflow.queue_stream
    group = settings.workflow.queue_group
    min_delay: float | None = None

    for message_id, raw_fields in messages:
        try:
            envelope = _decode_envelope(message_id, raw_fields)
        except Exception as exc:
            queue_failed_total.inc()
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
            await _ack_and_delete(redis, stream=stream, group=group, message_id=envelope.message_id)
            continue

        try:
            delay = await _handle_envelope(redis, settings=settings, envelope=envelope)
            min_delay = _merge_min_delay(min_delay, delay)
        except Exception:
            queue_failed_total.inc()
            log.exception(
                "queue.worker.handle_message_failed",
                message_id=envelope.message_id,
                attempt=envelope.attempt,
            )

    return min_delay


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
    stream = settings.workflow.queue_stream
    group = settings.workflow.queue_group
    consumer = _consumer_name(settings)
    await _ensure_group(redis, stream=stream, group=group)

    consecutive_failures = 0
    _BACKOFF_BASE = 0.3
    _BACKOFF_CAP = 30.0

    while not stop_event.is_set():
        try:
            min_delay: float | None = None

            claimed = await _claim_stale_pending(
                redis,
                stream=stream,
                group=group,
                consumer=consumer,
                count=settings.workflow.queue_read_count,
            )
            min_delay = _merge_min_delay(
                min_delay,
                await _process_messages(redis, settings=settings, messages=claimed),
            )

            pending = await _read_own_pending(
                redis,
                stream=stream,
                group=group,
                consumer=consumer,
                count=settings.workflow.queue_read_count,
            )
            min_delay = _merge_min_delay(
                min_delay,
                await _process_messages(redis, settings=settings, messages=pending),
            )

            block_ms = 1 if (claimed or pending) else settings.workflow.queue_read_block_ms
            new_messages = await _read_new_messages(
                redis,
                stream=stream,
                group=group,
                consumer=consumer,
                count=settings.workflow.queue_read_count,
                block_ms=block_ms,
            )
            min_delay = _merge_min_delay(
                min_delay,
                await _process_messages(redis, settings=settings, messages=new_messages),
            )

            consecutive_failures = 0

            if min_delay is not None and min_delay > 0:
                await asyncio.sleep(min(min_delay, 1.0))
        except asyncio.CancelledError:  # pragma: no cover
            raise
        except Exception:
            consecutive_failures += 1
            log.exception("queue.worker.loop_error", consecutive_failures=consecutive_failures)

            try:
                await redis.ping()
            except Exception:
                log.error(
                    "queue.worker.redis_unreachable",
                    detail="Redis ping failed; connection may be stale.",
                    consecutive_failures=consecutive_failures,
                )

            backoff = min(_BACKOFF_BASE * (2 ** (consecutive_failures - 1)), _BACKOFF_CAP)
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
        await asyncio.wait_for(task, timeout=timeout)
    except TimeoutError:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:
            pass
    finally:
        async with _worker_lifecycle_guard:
            if _worker_task is task:
                _worker_task = None
                _worker_stop_event = None


def _pending_count(raw: Any) -> int:
    if isinstance(raw, dict):
        value = raw.get("pending")
        if isinstance(value, int):
            return value
    value = getattr(raw, "pending", None)
    if isinstance(value, int):
        return value
    return 0


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

    redis = await _get_redis(settings)
    stream = settings.workflow.queue_stream
    group = settings.workflow.queue_group
    dlq_stream = settings.workflow.queue_dlq_stream
    await _ensure_group(redis, stream=stream, group=group)
    queue_depth = int(await redis.xlen(stream))
    dlq_depth = int(await redis.xlen(dlq_stream))
    pending_raw = await redis.xpending(stream, group)
    pending = _pending_count(pending_raw)
    queue_pending_count.set(pending)

    return {
        "execution_backend": execution_backend,
        "queue_enabled": True,
        "stream": stream,
        "group": group,
        "consumer": _consumer_name(settings),
        "queue_depth": queue_depth,
        "pending": pending,
        "dlq_stream": dlq_stream,
        "dlq_depth": dlq_depth,
        "retry_max_attempts": settings.workflow.queue_retry_max_attempts,
        "history_stream": settings.workflow.history_stream,
        "history_retention_maxlen": settings.workflow.history_retention_maxlen,
    }


async def drain_dlq(settings: Settings, *, limit: int = 100) -> int:
    """Delete DLQ stream entries without replaying them."""
    if limit < 1:
        return 0
    bounded_limit = min(int(limit), 1000)

    redis = await _get_redis(settings)
    dlq_stream = settings.workflow.queue_dlq_stream
    entries = await redis.xrange(dlq_stream, min="-", max="+", count=bounded_limit)
    if not entries:
        return 0

    ids = [_as_str(entry_id) for entry_id, _ in entries]

    pipeline = redis.pipeline(transaction=False)
    for entry_id in ids:
        pipeline.xdel(dlq_stream, entry_id)
    await pipeline.execute()
    return len(ids)


async def replay_dlq(settings: Settings, *, limit: int = 10) -> int:
    """Re-enqueue DLQ entries as fresh jobs with reset attempt counter."""
    if limit < 1:
        return 0
    bounded_limit = min(int(limit), 1000)

    redis = await _get_redis(settings)
    dlq_stream = settings.workflow.queue_dlq_stream
    entries = await redis.xrange(
        dlq_stream,
        min="-",
        max="+",
        count=bounded_limit,
    )
    if not entries:
        return 0

    replayed = 0
    for entry_id, raw_fields in entries:
        fields = {_as_str(k): v for k, v in raw_fields.items()}
        payload_raw = _as_str(fields.get("payload_json", "{}"))
        try:
            payload = json.loads(payload_raw)
            if not isinstance(payload, dict):
                continue
        except Exception:
            continue

        # Reset delivery_id to None so the replayed job bypasses idempotency checks.
        await enqueue_ticket_job(
            delivery_id=None,
            payload=payload,
            settings=settings,
            attempt=0,
        )
        await redis.xdel(dlq_stream, _as_str(entry_id))
        replayed += 1

    return replayed


async def aclose_queue_clients() -> None:
    from zammad_pdf_archiver.adapters.redis_pool import close_all

    await close_all()
