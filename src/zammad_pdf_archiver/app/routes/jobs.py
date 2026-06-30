from __future__ import annotations

from fastapi import APIRouter

from zammad_pdf_archiver.app.jobs.history import read_history

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("/history")
def job_history(
    limit: int = 100,
    ticket_id: int | None = None,
) -> dict[str, object]:
    entries = read_history(limit=limit, ticket_id=ticket_id)
    return {"entries": entries}
