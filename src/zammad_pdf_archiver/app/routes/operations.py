from __future__ import annotations

from zammad_pdf_archiver.app.jobs.history import _history_enabled, read_history
from zammad_pdf_archiver.app.jobs.redis_queue import drain_dlq
from zammad_pdf_archiver.config.settings import Settings


async def history_payload(
    settings: Settings,
    *,
    limit: int,
    ticket_id: int | None = None,
) -> dict[str, object]:
    if not _history_enabled(settings):
        return {
            "status": "disabled",
            "available": False,
            "count": 0,
            "truncated": False,
            "items": [],
        }

    bounded_limit = max(1, min(int(limit), 5000))
    items = await read_history(settings, limit=bounded_limit, ticket_id=ticket_id)
    return {
        "status": "ok",
        "available": True,
        "count": len(items),
        "truncated": len(items) == bounded_limit,
        "items": items,
    }


async def drain_dlq_payload(settings: Settings, *, limit: int) -> dict[str, object]:
    drain_result = await drain_dlq(settings, limit=limit)
    status = "partial" if drain_result["not_deleted"] else "ok"
    return {"status": status, "drained": drain_result["deleted"], **drain_result}
