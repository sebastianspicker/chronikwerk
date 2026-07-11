from __future__ import annotations

import asyncio
from typing import Any

import structlog
from fastapi import APIRouter, Path, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator
from starlette.responses import JSONResponse

from zammad_pdf_archiver.app.constants import (
    DELIVERY_ID_HEADER,
    FORCE_REPROCESS_KEY,
    REQUEST_ID_KEY,
)
from zammad_pdf_archiver.app.jobs.admission import AdmissionClosed, JobAdmission
from zammad_pdf_archiver.app.jobs.history import record_history_event
from zammad_pdf_archiver.app.jobs.process_ticket import process_ticket
from zammad_pdf_archiver.app.jobs.shutdown import is_shutting_down, track_task
from zammad_pdf_archiver.app.responses import api_error, settings_or_503, verify_bearer_token
from zammad_pdf_archiver.config.settings import Settings
from zammad_pdf_archiver.domain.ticket_id import extract_ticket_id

router = APIRouter()

log = structlog.get_logger(__name__)

# Security: explicit upper bound on batch size to prevent resource exhaustion.
# The body-size middleware provides some protection, but this is defense-in-depth.
MAX_BATCH_SIZE: int = 100


class IngestPayload(BaseModel):
    """Minimal webhook payload schema: require resolvable ticket id; allow extra fields."""

    model_config = ConfigDict(extra="allow")

    ticket: dict[str, Any] | None = None
    # Security: reject non-positive ticket IDs at the schema level (defense-in-depth).
    ticket_id: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _require_ticket_id(self) -> IngestPayload:
        tid = self.resolved_ticket_id()
        if tid is None or tid < 1:
            raise ValueError("Payload must contain ticket.id or ticket_id (positive integer)")
        return self

    def resolved_ticket_id(self) -> int | None:
        return extract_ticket_id(self.model_dump())


def _public_payload_for_job(payload: IngestPayload, request_id: str | None) -> dict[str, Any]:
    payload_for_job = payload.model_dump()
    payload_for_job.pop(FORCE_REPROCESS_KEY, None)
    payload_for_job[REQUEST_ID_KEY] = request_id
    return payload_for_job


def _normalized_delivery_id(value: str | None) -> str | None:
    return (value or "").strip() or None


async def _run_process_ticket_background(
    *,
    delivery_id: str | None,
    payload: dict[str, Any],
    settings: Settings,
    admission: JobAdmission,
) -> None:
    ticket_id = extract_ticket_id(payload)
    if ticket_id is None:
        log.warning("ingest.skip_background_no_ticket_id", delivery_id=delivery_id)
        admission.cancel_reservation()
        return

    bound: dict[str, object] = {"ticket_id": ticket_id}
    if delivery_id:
        bound["delivery_id"] = delivery_id

    try:
        await admission.acquire()
    except AdmissionClosed:
        log.info("ingest.job_cancelled_during_shutdown", ticket_id=ticket_id)
        return

    structlog.contextvars.bind_contextvars(**bound)
    try:
        await process_ticket(delivery_id, payload, settings)
    except Exception:  # pylint: disable=broad-exception-caught
        log.exception(
            "ingest.process_ticket_unhandled_error",
            ticket_id=ticket_id,
            delivery_id=delivery_id,
        )
    finally:
        structlog.contextvars.unbind_contextvars(*bound.keys())
        await admission.release()


def _schedule_background_task(
    *,
    delivery_id: str | None,
    payload: dict[str, Any],
    settings: Settings,
    admission: JobAdmission,
) -> bool:
    ticket_id = extract_ticket_id(payload)
    if not admission.try_reserve():
        return False
    try:
        task = asyncio.create_task(
            _run_process_ticket_background(
                delivery_id=delivery_id,
                payload=payload,
                settings=settings,
                admission=admission,
            )
        )
    except Exception:
        admission.cancel_reservation()
        raise
    track_task(task)
    record_history_event(
        "accepted",
        ticket_id,
        delivery_id=delivery_id,
        request_id=(
            str(payload.get(REQUEST_ID_KEY)) if payload.get(REQUEST_ID_KEY) is not None else None
        ),
    )
    return True


def _overload_error() -> JSONResponse:
    response = api_error(
        503,
        "Service is at background job capacity; retry later.",
        code="job_capacity_exhausted",
    )
    response.headers["Retry-After"] = "1"
    return response


def _resolve_settings_or_error(request: Request) -> tuple[Settings | None, JSONResponse | None]:
    if is_shutting_down():
        return None, api_error(503, "Service is shutting down", code="shutting_down")
    settings: Settings | None = getattr(request.app.state, "settings", None)
    if settings is None:
        return None, api_error(503, "settings not configured", code="settings_not_configured")
    return settings, None


@router.post("/ingest", status_code=202)
async def ingest_webhook(
    request: Request,
    payload: IngestPayload,
    dry_run: bool = False,
) -> JSONResponse:
    """Accept a single Zammad webhook payload and dispatch it for ticket archival."""
    settings, error = _resolve_settings_or_error(request)
    if error is not None:
        return error
    if settings is None:
        return api_error(503, "settings not configured", code="settings_not_configured")
    admission: JobAdmission = request.app.state.admission

    ticket_id = payload.resolved_ticket_id()
    if dry_run:
        return JSONResponse(
            status_code=202,
            content={"status": "dry_run_accepted", "ticket_id": ticket_id},
        )

    if ticket_id is not None:
        delivery_id = _normalized_delivery_id(request.headers.get(DELIVERY_ID_HEADER))
        payload_for_job = _public_payload_for_job(
            payload,
            getattr(request.state, "request_id", None),
        )
        ticket_id = extract_ticket_id(payload_for_job)
        if ticket_id is not None:
            if not _schedule_background_task(
                delivery_id=delivery_id,
                payload=payload_for_job,
                settings=settings,
                admission=admission,
            ):
                return _overload_error()

    return JSONResponse(status_code=202, content={"status": "accepted", "ticket_id": ticket_id})


@router.post("/ingest/batch", status_code=202)
async def batch_ingest(
    request: Request,
    payloads: list[IngestPayload],
    dry_run: bool = False,
) -> JSONResponse:
    """Accept a batch of webhook payloads and dispatch each for ticket archival."""
    settings, error = _resolve_settings_or_error(request)
    if error is not None:
        return error
    if settings is None:
        return api_error(503, "settings not configured", code="settings_not_configured")
    admission: JobAdmission = request.app.state.admission

    # Security: reject oversized batches before processing any items.
    if len(payloads) > MAX_BATCH_SIZE:
        return api_error(
            422,
            f"batch too large (max {MAX_BATCH_SIZE} items)",
            code="batch_too_large",
        )

    if dry_run:
        return JSONResponse(
            status_code=202,
            content={"status": "dry_run_accepted", "count": len(payloads)},
        )

    jobs: list[tuple[str | None, dict[str, Any]]] = []
    batch_delivery_id = _normalized_delivery_id(request.headers.get(DELIVERY_ID_HEADER))
    for index, payload in enumerate(payloads):
        ticket_id = payload.resolved_ticket_id()
        if ticket_id is not None:
            payload_for_job = _public_payload_for_job(
                payload,
                getattr(request.state, "request_id", None),
            )
            delivery_id = f"{batch_delivery_id}:{index}" if batch_delivery_id is not None else None
            jobs.append((delivery_id, payload_for_job))

    if jobs and not admission.try_reserve(len(jobs)):
        return _overload_error()

    created = 0
    try:
        for delivery_id, payload_for_job in jobs:
            task = asyncio.create_task(
                _run_process_ticket_background(
                    delivery_id=delivery_id,
                    payload=payload_for_job,
                    settings=settings,
                    admission=admission,
                )
            )
            track_task(task)
            created += 1
    except Exception:
        admission.cancel_reservation(len(jobs) - created)
        raise

    return JSONResponse(status_code=202, content={"status": "accepted", "count": len(jobs)})


@router.post("/retry/{ticket_id}", status_code=202)
async def retry_ticket(
    request: Request,
    # Security: reject non-positive ticket IDs at the parameter level.
    ticket_id: int = Path(..., ge=1),
) -> JSONResponse:
    """Force reprocessing of a ticket by ID, bypassing idempotency checks."""
    settings = settings_or_503(request)
    verify_bearer_token(
        request,
        settings.retry_bearer_token,
        missing_detail="retry_token_not_configured",
    )

    if not schedule_retry(request, ticket_id=ticket_id, settings=settings):
        return _overload_error()

    return JSONResponse(status_code=202, content={"status": "accepted", "ticket_id": ticket_id})


def schedule_retry(request: Request, *, ticket_id: int, settings: Settings) -> bool:
    """Schedule one forced retry for existing and admin route adapters."""
    payload_for_job: dict[str, Any] = {"ticket_id": ticket_id}
    payload_for_job[REQUEST_ID_KEY] = getattr(request.state, "request_id", None)
    payload_for_job[FORCE_REPROCESS_KEY] = True
    admission: JobAdmission = request.app.state.admission
    return _schedule_background_task(
        delivery_id=None,  # Retry does not need deduplication
        payload=payload_for_job,
        settings=settings,
        admission=admission,
    )
