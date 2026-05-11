from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from zammad_pdf_archiver.app.jobs.history import read_history
from zammad_pdf_archiver.app.jobs.redis_queue import drain_dlq, get_queue_stats
from zammad_pdf_archiver.app.jobs.shutdown import is_shutting_down
from zammad_pdf_archiver.app.jobs.ticket_stores import is_ticket_in_flight
from zammad_pdf_archiver.app.responses import settings_or_503, verify_bearer_auth

router = APIRouter()


@router.get("/jobs/queue/stats")
async def get_queue_status(request: Request) -> dict[str, bool | int | str]:
    """Return current queue statistics for the configured execution backend."""
    settings = settings_or_503(request)
    verify_bearer_auth(request, settings)
    try:
        stats = await get_queue_stats(settings)
    except Exception:
        return {
            "execution_backend": (settings.workflow.execution_backend or "inprocess"),
            "queue_enabled": False,
            "status": "error",
            "detail": "queue_unavailable",
        }
    return {str(k): v for k, v in stats.items()}


@router.get("/jobs/history")
async def get_job_history(
    request: Request,
    limit: int = 100,
    ticket_id: int | None = None,
) -> dict[str, int | str | list[dict[str, object]]]:
    """Return recent job history events, optionally filtered by ticket ID."""
    settings = settings_or_503(request)
    verify_bearer_auth(request, settings)

    bounded_limit = max(1, min(int(limit), 5000))
    try:
        items = await read_history(
            settings,
            limit=bounded_limit,
            ticket_id=ticket_id,
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail="history_unavailable") from exc
    return {"status": "ok", "count": len(items), "items": items}


@router.post("/jobs/queue/dlq/drain")
async def drain_queue_dlq(request: Request, limit: int = 100) -> dict[str, int | str]:
    """Delete up to limit messages from the dead-letter queue without replaying them."""
    settings = settings_or_503(request)
    verify_bearer_auth(request, settings)
    bounded_limit = max(1, min(int(limit), 1000))
    try:
        drained = await drain_dlq(settings, limit=bounded_limit)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="dlq_unavailable") from exc
    return {"status": "ok", "drained": drained}


@router.get("/jobs/{ticket_id}")
async def get_job_status(request: Request, ticket_id: int) -> dict[str, bool | int]:
    """Return the in-flight status and shutdown state for a specific ticket ID."""
    settings = settings_or_503(request)
    verify_bearer_auth(request, settings)
    return {
        "ticket_id": ticket_id,
        "in_flight": is_ticket_in_flight(ticket_id),
        "shutting_down": is_shutting_down(),
    }
