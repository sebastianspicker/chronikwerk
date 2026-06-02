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
from zammad_pdf_archiver.app.jobs.process_ticket import process_ticket
from zammad_pdf_archiver.app.jobs.redis_queue import enqueue_ticket_job
from zammad_pdf_archiver.app.jobs.shutdown import is_shutting_down, track_task
from zammad_pdf_archiver.app.responses import api_error, settings_or_503, verify_bearer_auth
from zammad_pdf_archiver.config.settings import Settings
from zammad_pdf_archiver.domain.ticket_id import extract_ticket_id
from zammad_pdf_archiver.observability.metrics import history_record_failed_total

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


class IngestAcceptedResponse(BaseModel):
    status: str
    ticket_id: int | None


class BatchIngestAcceptedResponse(BaseModel):
    status: str
    count: int


def _public_payload_for_job(payload: IngestPayload, request_id: str | None) -> dict[str, Any]:
    """Build the internal job payload for public ingest requests.

    Public webhooks must not be able to set the internal force-reprocess flag; only
    authenticated retry/admin surfaces may bypass trigger-tag and signed-tag checks.
    """
    payload_for_job = payload.model_dump()
    payload_for_job.pop(FORCE_REPROCESS_KEY, None)
    payload_for_job[REQUEST_ID_KEY] = request_id
    return payload_for_job


def _normalized_delivery_id(value: str | None) -> str | None:
    return (value or "").strip() or None


def _batch_item_delivery_id(batch_delivery_id: str | None, index: int) -> str | None:
    if batch_delivery_id is None:
        return None
    # One delivery header represents the batch request; suffix with the item index so
    # idempotency is still tracked per ticket payload.
    return ":".join((batch_delivery_id, str(index)))


def _batch_too_large_response() -> JSONResponse:
    return api_error(
        422,
        f"batch too large (max {MAX_BATCH_SIZE} items)",
        code="batch_too_large",
    )


def _batch_dispatch_failure_response(
    *,
    accepted: int,
    failed_index: int,
    ticket_id: int,
) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={
            "status": "partial_failure" if accepted else "failed",
            "code": "batch_dispatch_failed",
            "accepted": accepted,
            "failed_index": failed_index,
            "failed_ticket_id": ticket_id,
        },
    )


async def _run_process_ticket_background(
    *,
    delivery_id: str | None,
    payload: dict[str, Any],
    settings: Settings,
) -> None:
    ticket_id = extract_ticket_id(payload)
    if ticket_id is None:
        log.warning("ingest.skip_background_no_ticket_id", delivery_id=delivery_id)
        return

    bound: dict[str, object] = {"ticket_id": ticket_id}
    if delivery_id:
        bound["delivery_id"] = delivery_id

    structlog.contextvars.bind_contextvars(**bound)
    try:
        result = await process_ticket(delivery_id, payload, settings)
        if result.history_recorded is False:
            history_record_failed_total.inc()
            log.warning(
                "process_ticket.history_not_recorded",
                ticket_id=result.ticket_id,
                delivery_id=delivery_id,
            )
        if result.lock_release_failed:
            log.warning(
                "ingest.ticket_lock_release_failed",
                ticket_id=result.ticket_id,
                delivery_id=delivery_id,
            )
    except Exception:
        log.exception(
            "ingest.process_ticket_unhandled_error",
            ticket_id=ticket_id,
            delivery_id=delivery_id,
        )
    finally:
        structlog.contextvars.unbind_contextvars(*bound.keys())


def _resolve_settings_or_error(request: Request) -> tuple[Settings | None, JSONResponse | None]:
    if is_shutting_down():
        return None, api_error(503, "Service is shutting down", code="shutting_down")
    settings: Settings | None = getattr(request.app.state, "settings", None)
    if settings is None:
        return None, api_error(503, "settings not configured", code="settings_not_configured")
    return settings, None


def _execution_backend(settings: Settings) -> str:
    return (settings.workflow.execution_backend or "inprocess").strip().lower()


async def dispatch_ticket(
    *,
    delivery_id: str | None,
    payload_for_job: dict[str, Any],
    settings: Settings,
) -> None:
    if _execution_backend(settings) == "redis_queue":
        await enqueue_ticket_job(
            delivery_id=delivery_id,
            payload=payload_for_job,
            settings=settings,
        )
        return

    task = asyncio.create_task(
        _run_process_ticket_background(
            delivery_id=delivery_id,
            payload=payload_for_job,
            settings=settings,
        )
    )
    track_task(task)


async def _dispatch_batch_payloads(
    *,
    request: Request,
    payloads: list[IngestPayload],
    settings: Settings,
) -> tuple[int, JSONResponse | None]:
    accepted = 0
    batch_delivery_id = _normalized_delivery_id(request.headers.get(DELIVERY_ID_HEADER))
    request_id = getattr(request.state, "request_id", None)

    for index, payload in enumerate(payloads):
        ticket_id = payload.resolved_ticket_id()
        if ticket_id is None:
            continue

        try:
            await dispatch_ticket(
                delivery_id=_batch_item_delivery_id(batch_delivery_id, index),
                payload_for_job=_public_payload_for_job(payload, request_id),
                settings=settings,
            )
        except Exception:
            log.exception(
                "ingest.batch_dispatch_failed",
                accepted=accepted,
                failed_index=index,
                ticket_id=ticket_id,
            )
            return accepted, _batch_dispatch_failure_response(
                accepted=accepted,
                failed_index=index,
                ticket_id=ticket_id,
            )
        accepted += 1

    return accepted, None


@router.post("/ingest", status_code=202, response_model=IngestAcceptedResponse)
async def ingest_webhook(
    request: Request,
    payload: IngestPayload,
) -> JSONResponse:
    """Accept a single Zammad webhook payload and dispatch it for ticket archival."""
    settings, error = _resolve_settings_or_error(request)
    if error is not None:
        return error
    if settings is None:
        return api_error(503, "settings not configured", code="settings_not_configured")

    ticket_id = payload.resolved_ticket_id()
    if ticket_id is not None:
        delivery_id = _normalized_delivery_id(request.headers.get(DELIVERY_ID_HEADER))
        payload_for_job = _public_payload_for_job(
            payload,
            getattr(request.state, "request_id", None),
        )
        await dispatch_ticket(
            delivery_id=delivery_id,
            payload_for_job=payload_for_job,
            settings=settings,
        )

    return JSONResponse(status_code=202, content={"status": "accepted", "ticket_id": ticket_id})


@router.post("/ingest/batch", status_code=202, response_model=BatchIngestAcceptedResponse)
async def batch_ingest(
    request: Request,
    payloads: list[IngestPayload],
) -> JSONResponse:
    """Accept a batch of webhook payloads and dispatch each for ticket archival."""
    settings, error = _resolve_settings_or_error(request)
    if error is not None:
        return error
    if settings is None:
        return api_error(503, "settings not configured", code="settings_not_configured")

    # Security: reject oversized batches before processing any items.
    if len(payloads) > MAX_BATCH_SIZE:
        return _batch_too_large_response()

    accepted, error_response = await _dispatch_batch_payloads(
        request=request,
        payloads=payloads,
        settings=settings,
    )
    if error_response is not None:
        return error_response

    return JSONResponse(status_code=202, content={"status": "accepted", "count": accepted})


@router.post("/retry/{ticket_id}", status_code=202)
async def retry_ticket(
    request: Request,
    # Security: reject non-positive ticket IDs at the parameter level.
    ticket_id: int = Path(..., ge=1),
) -> JSONResponse:
    """Force reprocessing of a ticket by ID, bypassing idempotency checks."""
    settings = settings_or_503(request)
    verify_bearer_auth(request, settings)

    payload_for_job: dict[str, Any] = {"ticket_id": ticket_id}
    payload_for_job[REQUEST_ID_KEY] = getattr(request.state, "request_id", None)
    payload_for_job[FORCE_REPROCESS_KEY] = True
    await dispatch_ticket(
        delivery_id=None,  # Retry does not need deduplication
        payload_for_job=payload_for_job,
        settings=settings,
    )

    return JSONResponse(status_code=202, content={"status": "accepted", "ticket_id": ticket_id})
