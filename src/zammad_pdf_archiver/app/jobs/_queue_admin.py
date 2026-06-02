"""Admin and DLQ helpers for the Redis queue backend."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from zammad_pdf_archiver.app.jobs._queue_stream import _ensure_group
from zammad_pdf_archiver.app.jobs._queue_types import _as_str
from zammad_pdf_archiver.config.settings import Settings

GetRedis = Callable[[Settings], Any]
EnqueueTicketJob = Callable[..., Any]
ConsumerName = Callable[[Settings], str]


def pending_count(raw: Any) -> int:
    if isinstance(raw, dict):
        value = raw.get("pending")
        if isinstance(value, int):
            return value
    value = getattr(raw, "pending", None)
    if isinstance(value, int):
        return value
    return 0


async def get_queue_stats(
    settings: Settings,
    *,
    get_redis_client: GetRedis,
    consumer_name: ConsumerName,
    queue_pending_count: Any,
) -> dict[str, Any]:
    redis = await get_redis_client(settings)
    stream = settings.workflow.queue_stream
    group = settings.workflow.queue_group
    dlq_stream = settings.workflow.queue_dlq_stream
    await _ensure_group(redis, stream=stream, group=group)
    queue_depth = int(await redis.xlen(stream))
    dlq_depth = int(await redis.xlen(dlq_stream))
    pending_raw = await redis.xpending(stream, group)
    pending = pending_count(pending_raw)
    queue_pending_count.set(pending)

    return {
        "execution_backend": "redis_queue",
        "queue_enabled": True,
        "stream": stream,
        "group": group,
        "consumer": consumer_name(settings),
        "queue_depth": queue_depth,
        "pending": pending,
        "dlq_stream": dlq_stream,
        "dlq_depth": dlq_depth,
        "retry_max_attempts": settings.workflow.queue_retry_max_attempts,
        "history_stream": settings.workflow.history_stream,
        "history_retention_maxlen": settings.workflow.history_retention_maxlen,
    }


async def drain_dlq(
    settings: Settings,
    *,
    get_redis_client: GetRedis,
    limit: int = 100,
) -> dict[str, int]:
    """Delete DLQ stream entries without replaying them."""
    result = {"selected": 0, "deleted": 0, "not_deleted": 0}
    if limit < 1:
        return result
    bounded_limit = min(int(limit), 1000)

    redis = await get_redis_client(settings)
    dlq_stream = settings.workflow.queue_dlq_stream
    entries = await redis.xrange(dlq_stream, min="-", max="+", count=bounded_limit)
    if not entries:
        return result

    ids = [_as_str(entry_id) for entry_id, _ in entries]
    result["selected"] = len(ids)

    pipeline = redis.pipeline(transaction=False)
    for entry_id in ids:
        pipeline.xdel(dlq_stream, entry_id)
    delete_results = await pipeline.execute()
    deleted = min(sum(int(count) for count in delete_results), len(ids))
    result["deleted"] = deleted
    result["not_deleted"] = len(ids) - deleted
    return result


async def replay_dlq(
    settings: Settings,
    *,
    get_redis_client: GetRedis,
    enqueue_ticket_job: EnqueueTicketJob,
    log: Any,
    limit: int = 10,
) -> dict[str, int]:
    """Re-enqueue DLQ entries as fresh jobs with reset attempt counter."""
    result = empty_replay_dlq_result()
    if limit < 1:
        return result
    bounded_limit = min(int(limit), 1000)

    redis = await get_redis_client(settings)
    dlq_stream = settings.workflow.queue_dlq_stream
    entries = await redis.xrange(
        dlq_stream,
        min="-",
        max="+",
        count=bounded_limit,
    )
    if not entries:
        return result

    result["selected"] = len(entries)
    for entry_id, raw_fields in entries:
        await replay_dlq_entry(
            redis,
            settings=settings,
            dlq_stream=dlq_stream,
            enqueue_ticket_job=enqueue_ticket_job,
            log=log,
            result=result,
            entry_id=entry_id,
            raw_fields=raw_fields,
        )

    return result


def empty_replay_dlq_result() -> dict[str, int]:
    return {
        "selected": 0,
        "replayed": 0,
        "deleted": 0,
        "skipped": 0,
        "errors": 0,
        "not_deleted": 0,
    }


async def replay_dlq_entry(
    redis: Any,
    *,
    settings: Settings,
    dlq_stream: str,
    enqueue_ticket_job: EnqueueTicketJob,
    log: Any,
    result: dict[str, int],
    entry_id: Any,
    raw_fields: dict[Any, Any],
) -> None:
    entry_id_str = _as_str(entry_id)
    payload = decode_dlq_payload(raw_fields, entry_id_str=entry_id_str, log=log)
    if payload is None:
        result["skipped"] += 1
        return

    if not await enqueue_replayed_dlq_payload(
        settings,
        enqueue_ticket_job=enqueue_ticket_job,
        log=log,
        payload=payload,
        entry_id=entry_id_str,
    ):
        result["errors"] += 1
        return

    result["replayed"] += 1
    await delete_replayed_dlq_entry(
        redis,
        dlq_stream,
        entry_id_str=entry_id_str,
        result=result,
        log=log,
    )


def decode_dlq_payload(
    raw_fields: dict[Any, Any], *, entry_id_str: str, log: Any
) -> dict[str, Any] | None:
    fields = {_as_str(k): v for k, v in raw_fields.items()}
    payload_raw = _as_str(fields.get("payload_json", "{}"))
    try:
        payload = json.loads(payload_raw)
    except Exception:
        log.warning("queue.dlq_replay_skipped_invalid_payload", entry_id=entry_id_str)
        return None
    if not isinstance(payload, dict):
        log.warning("queue.dlq_replay_skipped_invalid_payload", entry_id=entry_id_str)
        return None
    return payload


async def enqueue_replayed_dlq_payload(
    settings: Settings,
    *,
    enqueue_ticket_job: EnqueueTicketJob,
    log: Any,
    payload: dict[str, Any],
    entry_id: str,
) -> bool:
    # Reset delivery_id to None so the replayed job bypasses idempotency checks.
    try:
        await enqueue_ticket_job(
            delivery_id=None,
            payload=payload,
            settings=settings,
            attempt=0,
        )
    except Exception:
        log.exception("queue.dlq_replay_enqueue_failed", entry_id=entry_id)
        return False
    return True


async def delete_replayed_dlq_entry(
    redis: Any,
    dlq_stream: str,
    *,
    entry_id_str: str,
    result: dict[str, int],
    log: Any,
) -> None:
    try:
        deleted = int(await redis.xdel(dlq_stream, entry_id_str))
    except Exception:
        result["errors"] += 1
        result["not_deleted"] += 1
        log.exception("queue.dlq_replay_delete_failed", entry_id=entry_id_str)
        return

    if deleted:
        result["deleted"] += min(deleted, 1)
        return
    result["not_deleted"] += 1
    log.warning("queue.dlq_replay_delete_unconfirmed", entry_id=entry_id_str)
