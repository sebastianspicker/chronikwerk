"""Redis stream I/O helpers: group management, ACK/delete, DLQ, and message reads.

All functions in this module accept a ``redis`` client as their first positional
argument.  They do NOT call ``_get_redis`` internally so they can be tested with
an injected fake Redis without needing monkeypatching.
"""

from __future__ import annotations

import json
import time
from typing import Any

import structlog

from zammad_pdf_archiver.adapters.redis_pool import import_redis
from zammad_pdf_archiver.app.jobs._queue_types import (
    _as_str,
    _parse_int,
    _QueueEnvelope,
)
from zammad_pdf_archiver.config.settings import Settings
from zammad_pdf_archiver.domain.exc_format import bounded_exc_message
from zammad_pdf_archiver.observability.metrics import (
    queue_dlq_total,
    queue_stale_pending_claim_failed_total,
)

log = structlog.get_logger(__name__)

_CLAIM_IDLE_MS = 30_000


async def _ensure_group(redis: Any, *, stream: str, group: str) -> None:
    """Create the consumer group idempotently (ignores BUSYGROUP if it already exists)."""
    _, ResponseError = import_redis()
    try:
        # Start at 0 so backlog existing before group creation is visible to consumers.
        await redis.xgroup_create(stream, group, id="0", mkstream=True)
    except ResponseError as exc:
        # BUSYGROUP Consumer Group name already exists
        if "BUSYGROUP" not in str(exc):
            raise


async def _ack_and_delete(redis: Any, *, stream: str, group: str, message_id: str) -> None:
    await redis.xack(stream, group, message_id)
    await redis.xdel(stream, message_id)


async def _push_dlq(
    redis: Any,
    *,
    settings: Settings,
    envelope: _QueueEnvelope,
    reason: str,
    error_message: str | None = None,
) -> None:
    fields: dict[str, str] = {
        "payload_json": json.dumps(
            envelope.payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ),
        "delivery_id": envelope.delivery_id or "",
        "attempt": str(envelope.attempt),
        "reason": reason,
        "failed_at": str(time.time()),
    }
    if envelope.enqueued_at:
        fields["enqueued_at"] = envelope.enqueued_at
    if error_message:
        fields["error"] = bounded_exc_message(error_message)
    await redis.xadd(settings.workflow.queue_dlq_stream, fields)
    queue_dlq_total.inc()


def _parse_stream_entries(records: Any, *, nested: bool) -> list[tuple[Any, Any]]:
    if not isinstance(records, list):
        return []

    entries = _flatten_nested_stream_entries(records) if nested else records
    out: list[tuple[Any, Any]] = []
    for message in entries:
        entry = _message_entry(message)
        if entry is not None:
            out.append(entry)
    return out


def _flatten_nested_stream_entries(records: list[Any]) -> list[Any]:
    entries: list[Any] = []
    for record in records:
        messages = _nested_stream_messages(record)
        if messages is not None:
            entries.extend(messages)
    return entries


def _nested_stream_messages(record: Any) -> list[Any] | None:
    if not isinstance(record, (list, tuple)) or len(record) != 2:
        return None
    _stream_name, messages = record
    return messages if isinstance(messages, list) else None


def _message_entry(message: Any) -> tuple[Any, Any] | None:
    if isinstance(message, (list, tuple)) and len(message) == 2:
        return (message[0], message[1])
    return None


async def _claim_stale_pending(
    redis: Any,
    *,
    stream: str,
    group: str,
    consumer: str,
    count: int,
    min_idle_ms: int = _CLAIM_IDLE_MS,
) -> list[tuple[Any, Any]]:
    """Steal messages from other consumers that have been idle too long (dead consumer recovery)."""
    try:
        pending_entries = await redis.xpending_range(stream, group, "-", "+", count)
    except Exception:
        queue_stale_pending_claim_failed_total.inc()
        log.warning(
            "queue.claim_stale_pending_failed",
            stream=stream,
            group=group,
            consumer=consumer,
        )
        raise

    message_ids = [
        message_id
        for entry in pending_entries
        if (message_id := _claimable_pending_message_id(entry, consumer, min_idle_ms)) is not None
    ]

    if not message_ids:
        return []

    claimed = await redis.xclaim(stream, group, consumer, min_idle_ms, message_ids)
    return _parse_stream_entries(claimed, nested=False)


def _claimable_pending_message_id(entry: Any, consumer: str, min_idle_ms: int) -> str | None:
    message_id = _as_str(_pending_entry_value(entry, "message_id") or "").strip()
    owner = _as_str(_pending_entry_value(entry, "consumer") or "").strip()
    idle_ms = _parse_int(_pending_entry_value(entry, "time_since_delivered"), default=0)

    if not message_id or owner == consumer or idle_ms < min_idle_ms:
        return None
    return message_id


def _pending_entry_value(entry: Any, key: str) -> Any:
    if isinstance(entry, dict):
        return entry.get(key)
    return getattr(entry, key, None)


async def _read_own_pending(
    redis: Any,
    *,
    stream: str,
    group: str,
    consumer: str,
    count: int,
) -> list[tuple[Any, Any]]:
    """Re-read messages already delivered to this consumer but not yet acknowledged."""
    records = await redis.xreadgroup(
        groupname=group,
        consumername=consumer,
        streams={stream: "0"},
        count=count,
    )
    return _parse_stream_entries(records, nested=True)


async def _read_new_messages(
    redis: Any,
    *,
    stream: str,
    group: str,
    consumer: str,
    count: int,
    block_ms: int,
) -> list[tuple[Any, Any]]:
    """Block-read for never-delivered messages from the stream."""
    records = await redis.xreadgroup(
        groupname=group,
        consumername=consumer,
        streams={stream: ">"},
        count=count,
        block=block_ms,
    )
    return _parse_stream_entries(records, nested=True)
