"""Coordinate one ticket archive from fetch through durable storage."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from time import perf_counter

import structlog

from chronikwerk.archiving.options import ArchiveRuntimeOptions
from chronikwerk.archiving.workflow import (
    ArchiveAttempt,
    ArchiveOutcome,
    ArchivePipelineRequest,
    EligibilityNotEstablishedError,
    cleanup_cancelled_pipeline,
    record_history,
    run_ticket_pipeline,
)
from chronikwerk.archiving.workflow_errors import (
    handle_ticket_pipeline_exception,
)
from chronikwerk.operations.guards import TicketGuards
from chronikwerk.operations.history import JobHistory
from chronikwerk.operations.job import TicketJob
from chronikwerk.operations.metrics import (
    skipped_total,
    total_seconds,
)
from chronikwerk.zammad.gateway import AsyncZammadClient

log = structlog.get_logger(__name__)


def build_ticket_processor(
    options: ArchiveRuntimeOptions,
    *,
    client: AsyncZammadClient,
    guards: TicketGuards,
    history: JobHistory,
) -> Callable[[TicketJob], Awaitable[ArchiveOutcome]]:
    """Bind immutable options and application-owned services once for scheduled jobs.

    The result satisfies ``operations.scheduling.TicketProcessor``.
    """

    async def process(job: TicketJob) -> ArchiveOutcome:
        return await process_ticket(job, options, client=client, guards=guards, history=history)

    return process


async def process_ticket(
    job: TicketJob,
    options: ArchiveRuntimeOptions,
    *,
    client: AsyncZammadClient,
    guards: TicketGuards,
    history: JobHistory,
) -> ArchiveOutcome:
    """Orchestrate the full ticket archival pipeline for a single admitted job."""
    request_id = job.request_id if job.request_id and job.request_id.strip() else None
    attempt = ArchiveAttempt(
        runtime=options,
        ticket_id=job.ticket_id,
        delivery_id=job.delivery_id,
        request_id=request_id,
        history=history,
    )
    record_history(attempt, status="running")

    with structlog.contextvars.bound_contextvars(**_bound_context(attempt)):
        return await _process_with_ticket_lock(attempt, job=job, client=client, guards=guards)


def _bound_context(attempt: ArchiveAttempt) -> dict[str, object]:
    bound: dict[str, object] = {"ticket_id": attempt.ticket_id}
    if attempt.delivery_id:
        bound["delivery_id"] = attempt.delivery_id
    if attempt.request_id:
        bound["request_id"] = attempt.request_id
    return bound


async def _process_with_ticket_lock(
    attempt: ArchiveAttempt,
    *,
    job: TicketJob,
    client: AsyncZammadClient,
    guards: TicketGuards,
) -> ArchiveOutcome:
    if not guards.try_acquire_ticket(attempt.ticket_id):
        return _skip_in_flight(attempt)

    try:
        claimed = _claim_delivery_or_skip(attempt, guards=guards)
        if claimed is not None:
            return claimed
        return await _process_ticket_using_client(attempt, job=job, client=client)
    finally:
        guards.release_ticket(attempt.ticket_id)


def _skip_in_flight(attempt: ArchiveAttempt) -> ArchiveOutcome:
    """Return a skip result when another worker is already processing this ticket."""
    log.info(
        "process_ticket.skip_ticket_in_flight",
        ticket_id=attempt.ticket_id,
        delivery_id=attempt.delivery_id,
    )
    skipped_total.labels(reason="in_flight").inc()
    record_history(attempt, status="skipped_in_flight")
    return ArchiveOutcome(status="skipped_in_flight", ticket_id=attempt.ticket_id)


def _claim_delivery_or_skip(
    attempt: ArchiveAttempt, *, guards: TicketGuards
) -> ArchiveOutcome | None:
    """Enforce at-most-once delivery; return a skip result for a claimed delivery."""
    if not attempt.delivery_id:
        return None
    if guards.try_claim_delivery(attempt.delivery_id):
        return None

    log.info(
        "process_ticket.skip_delivery_id_seen",
        ticket_id=attempt.ticket_id,
        delivery_id=attempt.delivery_id,
    )
    skipped_total.labels(reason="idempotency").inc()
    record_history(attempt, status="skipped_idempotency")
    return ArchiveOutcome(status="skipped_idempotency", ticket_id=attempt.ticket_id)


async def _process_ticket_using_client(
    attempt: ArchiveAttempt,
    *,
    job: TicketJob,
    client: AsyncZammadClient,
) -> ArchiveOutcome:
    """Run one archive attempt with the application-owned client."""
    request = ArchivePipelineRequest(
        client=client,
        attempt=attempt,
        payload=job.payload,
        force_reprocess=job.force_reprocess,
    )
    total_start = perf_counter()
    observe_total = True
    try:
        result, observe_total = await _run_pipeline_with_error_boundary(request)
        return result
    finally:
        if observe_total:
            total_seconds.observe(perf_counter() - total_start)


async def _run_pipeline_with_error_boundary(
    request: ArchivePipelineRequest,
) -> tuple[ArchiveOutcome, bool]:
    try:
        return await run_ticket_pipeline(request)
    except asyncio.CancelledError:
        raise
    except EligibilityNotEstablishedError as exc:
        return (
            await _handle_pipeline_failure(
                request,
                exc.cause,
                project_to_ticket=False,
            ),
            True,
        )
    except Exception as exc:  # pylint: disable=broad-exception-caught
        return (
            await _handle_pipeline_failure(request, exc),
            True,
        )


async def _handle_pipeline_failure(
    request: ArchivePipelineRequest,
    exc: Exception,
    *,
    project_to_ticket: bool = True,
) -> ArchiveOutcome:
    try:
        return await handle_ticket_pipeline_exception(
            client=request.client,
            attempt=request.attempt,
            trigger_tag=request.attempt.runtime.workflow.trigger_tag,
            exc=exc,
            project_to_ticket=project_to_ticket,
        )
    except asyncio.CancelledError:
        if project_to_ticket:
            await cleanup_cancelled_pipeline(request)
        raise
