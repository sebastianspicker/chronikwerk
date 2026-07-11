from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from zammad_pdf_archiver.app.jobs.history import read_history
from zammad_pdf_archiver.app.responses import verify_bearer_token

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("/history")
def job_history(
    request: Request,
    limit: int = 100,
    ticket_id: int | None = None,
) -> dict[str, object]:
    settings = getattr(request.app.state, "settings", None)
    if settings is None or not settings.observability.history_enabled:
        raise HTTPException(status_code=404, detail="not_found")
    verify_bearer_token(
        request,
        settings.observability.history_bearer_token,
        missing_detail="history_token_not_configured",
    )
    entries = read_history(limit=limit, ticket_id=ticket_id)
    return {"entries": entries}
