from __future__ import annotations

import time
from typing import Any

import structlog

from zammad_pdf_archiver.adapters.redis_pool import get_redis, import_redis_class
from zammad_pdf_archiver.app.jobs.history_entries import append_matching_history_entries
from zammad_pdf_archiver.config.settings import Settings
from zammad_pdf_archiver.domain.exc_format import bounded_exc_message

log = structlog.get_logger(__name__)


def _history_enabled(settings: Settings) -> bool:
    if not settings.workflow.redis_url:
        return False
    return int(settings.workflow.history_retention_maxlen) > 0


async def _redis_client(settings: Settings) -> Any | None:
    if not _history_enabled(settings):
        return None
    if import_redis_class() is None:
        return None
    redis_url = settings.workflow.redis_url
    if not redis_url:
        return None
    return await get_redis(redis_url)


async def record_history_event(
    settings: Settings,
    *,
    status: str,
    ticket_id: int | None,
    classification: str | None = None,
    message: str = "",
    delivery_id: str | None = None,
    request_id: str | None = None,
) -> bool:
    """Append a processing history event to the Redis stream; return True on success."""
    redis = await _redis_client(settings)
    if redis is None:
        return False

    fields: dict[str, str] = {
        "status": status,
        "ticket_id": str(ticket_id) if ticket_id is not None else "",
        "classification": classification or "",
        "message": bounded_exc_message(message),
        "delivery_id": delivery_id or "",
        "request_id": request_id or "",
        "created_at": str(time.time()),
    }

    stream = settings.workflow.history_stream
    maxlen = int(settings.workflow.history_retention_maxlen)
    try:
        await redis.xadd(stream, fields, maxlen=maxlen, approximate=True)
        return True
    except Exception:
        log.warning("history.record_failed", status=status, ticket_id=ticket_id)
        return False


async def read_history(
    settings: Settings,
    *,
    limit: int,
    ticket_id: int | None = None,
) -> list[dict[str, Any]]:
    """Read the most recent history events from the Redis stream, optionally filtered by ticket."""
    redis = await _redis_client(settings)
    if redis is None:
        if _history_enabled(settings):
            raise RuntimeError("history_unavailable")
        return []

    bounded_limit = max(1, min(int(limit), 5000))
    fetch_count = _history_fetch_count(bounded_limit, ticket_id=ticket_id)

    max_id = "+"
    out: list[dict[str, Any]] = []
    while len(out) < bounded_limit:
        entries = await _read_history_batch(
            redis,
            stream=settings.workflow.history_stream,
            max_id=max_id,
            fetch_count=fetch_count,
        )
        if not entries:
            break

        append_matching_history_entries(out, entries, ticket_id=ticket_id, limit=bounded_limit)
        if _history_scan_complete(entries, fetch_count=fetch_count, ticket_id=ticket_id):
            break
        max_id = _previous_history_max_id(entries)

    return out


def _history_fetch_count(bounded_limit: int, *, ticket_id: int | None) -> int:
    if ticket_id is None:
        return bounded_limit
    return min(max(bounded_limit, 100), 1000)


async def _read_history_batch(
    redis: Any,
    *,
    stream: str,
    max_id: str,
    fetch_count: int,
) -> list[tuple[Any, Any]]:
    try:
        return await redis.xrevrange(stream, max=max_id, min="-", count=fetch_count)
    except Exception as exc:
        log.warning("history.read_failed")
        raise RuntimeError("history_unavailable") from exc

def _history_scan_complete(
    entries: list[tuple[Any, Any]], *, fetch_count: int, ticket_id: int | None
) -> bool:
    return ticket_id is None or len(entries) < fetch_count


def _previous_history_max_id(entries: list[tuple[Any, Any]]) -> str:
    return f"({entries[-1][0]}"
