"""Internal success-path stages for processing one Zammad ticket."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import structlog

from chronikwerk._version import VERSION
from chronikwerk.adapters.storage.layout import (
    build_filename_from_pattern,
    build_target_dir,
)
from chronikwerk.adapters.zammad.client import AsyncZammadClient
from chronikwerk.adapters.zammad.models import TagList, Ticket
from chronikwerk.app.jobs.async_retry import async_retry
from chronikwerk.app.jobs.history import record_history_event
from chronikwerk.app.jobs.ticket_notes import SuccessNotePayload, success_note_html
from chronikwerk.app.jobs.ticket_path import (
    determine_username,
    parse_archive_path_segments,
)
from chronikwerk.app.jobs.ticket_renderer import build_and_render_pdf
from chronikwerk.app.jobs.ticket_storage import (
    StorageResult,
    StoreTicketFilesRequest,
    store_ticket_files,
)
from chronikwerk.config.settings import Settings
from chronikwerk.domain.async_work import run_sync_cancellation_safe
from chronikwerk.domain.state_machine import (
    apply_done,
    apply_error,
    apply_processing,
    should_process,
)
from chronikwerk.domain.time_utils import format_timestamp_utc, now_utc
from chronikwerk.observability.metrics import processed_total, skipped_total

log = structlog.get_logger(__name__)

_CANCELLATION_CLEANUP_TIMEOUT_SECONDS = 1.0


class CompletedTicketCancellation(asyncio.CancelledError):
    """Cancellation received after the archive and terminal tags were durable."""


@dataclass(frozen=True)
class TicketJobContext:
    """Common context threaded through the ticket processing pipeline."""

    settings: Settings
    ticket_id: int
    delivery_id: str | None
    request_id: str | None


@dataclass(frozen=True)
class ProcessTicketResult:
    """Record the terminal result returned by one ticket pipeline."""

    status: str
    ticket_id: int | None
    classification: str | None = None
    message: str = ""


@dataclass(frozen=True)
class _FetchedTicketState:
    ticket: Ticket
    tags: TagList


@dataclass(frozen=True)
class _PipelineOptions:
    """Group state-machine options that must remain consistent across stages."""

    trigger_tag: str
    require_trigger_tag: bool
    force_reprocess: bool


@dataclass(frozen=True)
class _SuccessNote:
    """Rendered success acknowledgement posted after terminal tags are durable."""

    subject: str
    html: str


async def _archive_fetched_ticket(
    *,
    client: AsyncZammadClient,
    ctx: TicketJobContext,
    payload: dict[str, Any],
    fetched: _FetchedTicketState,
    options: _PipelineOptions,
) -> None:
    """Apply processing state, persist the archive, and finalize terminal side effects."""
    await apply_processing(
        client,
        ctx.ticket_id,
        trigger_tag=options.trigger_tag,
        force_reprocess=options.force_reprocess,
    )

    now = now_utc()
    storage_paths = resolve_storage_paths(
        ctx,
        ticket=fetched.ticket,
        payload=payload,
        now=now,
    )
    storage_result = await render_and_store_ticket(
        client=client,
        ctx=ctx,
        ticket=fetched.ticket,
        tags=fetched.tags,
        storage_paths=storage_paths,
        now=now,
    )
    await finalize_success(
        client=client,
        ctx=ctx,
        trigger_tag=options.trigger_tag,
        now=now,
        storage_result=storage_result,
    )


def record_history(
    ctx: TicketJobContext,
    *,
    status: str,
    classification: str | None = None,
    message: str = "",
) -> None:
    """Record a bounded operator-visible event for this ticket execution."""
    try:
        record_history_event(
            status=status,
            ticket_id=ctx.ticket_id,
            classification=classification,
            message=message,
            delivery_id=ctx.delivery_id,
            request_id=ctx.request_id,
        )
    except Exception:  # pylint: disable=broad-exception-caught
        log.debug("process_ticket.history_record_failed", status=status, ticket_id=ctx.ticket_id)


async def run_ticket_pipeline(
    *,
    client: AsyncZammadClient,
    ctx: TicketJobContext,
    payload: dict[str, Any],
    trigger_tag: str,
    require_trigger_tag: bool,
    force_reprocess: bool,
) -> tuple[ProcessTicketResult, bool]:
    """Fetch, render, store, and finalize one ticket.

    Returns the result and whether total-time metrics should be observed.
    """
    options = _PipelineOptions(
        trigger_tag=trigger_tag,
        require_trigger_tag=require_trigger_tag,
        force_reprocess=force_reprocess,
    )
    return await _run_ticket_pipeline_with_options(
        client=client,
        ctx=ctx,
        payload=payload,
        options=options,
    )


async def _run_ticket_pipeline_with_options(
    *,
    client: AsyncZammadClient,
    ctx: TicketJobContext,
    payload: dict[str, Any],
    options: _PipelineOptions,
) -> tuple[ProcessTicketResult, bool]:
    fetched = await _fetch_ticket_state(client, ticket_id=ctx.ticket_id)
    # IMPORTANT: should_process is a non-atomic tag check. In multi-instance
    # deployments, a second worker may read the same tags before the first worker
    # writes PROCESSING_TAG. Multi-instance deployments must provide an external
    # serialization mechanism to prevent duplicate processing.
    if not options.force_reprocess and not should_process(
        fetched.tags.root,
        trigger_tag=options.trigger_tag,
        require_trigger_tag=options.require_trigger_tag,
    ):
        return (
            await skip_not_triggered(ctx, tags=fetched.tags.root),
            False,
        )

    try:
        await _archive_fetched_ticket(
            client=client,
            ctx=ctx,
            payload=payload,
            fetched=fetched,
            options=options,
        )
    except CompletedTicketCancellation:
        raise
    except asyncio.CancelledError:
        await cleanup_cancelled_pipeline(
            client=client,
            ctx=ctx,
            trigger_tag=options.trigger_tag,
        )
        raise
    return ProcessTicketResult(status="processed", ticket_id=ctx.ticket_id), True


async def _fetch_ticket_state(
    client: AsyncZammadClient,
    *,
    ticket_id: int,
) -> _FetchedTicketState:
    ticket = await client.get_ticket(ticket_id)
    tags = await client.list_tags(ticket_id)
    return _FetchedTicketState(ticket=ticket, tags=tags)


def resolve_storage_paths(
    ctx: TicketJobContext,
    *,
    ticket: Ticket,
    payload: dict[str, Any],
    now: datetime,
) -> tuple[Path, Path]:
    """Resolve validated storage locations before any output is written."""
    settings = ctx.settings
    custom_fields = (
        ticket.preferences.custom_fields
        if ticket.preferences is not None and isinstance(ticket.preferences.custom_fields, dict)
        else {}
    )
    username = determine_username(
        ticket=ticket,
        payload=payload,
        custom_fields=custom_fields,
        mode_field_name=settings.fields.archive_user_mode,
        archive_user_field_name=settings.fields.archive_user,
    )

    segments = parse_archive_path_segments(custom_fields.get(settings.fields.archive_path))
    target_dir = build_target_dir(
        settings.storage.root,
        username,
        segments,
    )
    filename = build_filename_from_pattern(
        settings.storage.filename_pattern,
        ticket_number=ticket.number,
        timestamp_utc=now.date().isoformat(),
    )
    target_path = target_dir / filename
    return target_path, target_path.with_name(target_path.name + ".json")


async def render_and_store_ticket(
    *,
    client: AsyncZammadClient,
    ctx: TicketJobContext,
    ticket: Ticket,
    tags: TagList,
    storage_paths: tuple[Path, Path],
    now: datetime,
) -> StorageResult:
    """Render, optionally sign, and atomically store a ticket PDF."""
    rendered = await build_and_render_pdf(
        client=client,
        ticket=ticket,
        tags=tags,
        ticket_id=ctx.ticket_id,
        settings=ctx.settings,
    )
    target_path, sidecar_path = storage_paths
    return await run_sync_cancellation_safe(
        store_ticket_files,
        StoreTicketFilesRequest(
            pdf_bytes=rendered.pdf_bytes,
            snapshot=rendered.snapshot,
            target_path=target_path,
            sidecar_path=sidecar_path,
            ticket_id=ticket.id,
            now=now,
            settings=ctx.settings,
            signing_cert_fingerprint=rendered.signing_cert_fingerprint,
        ),
    )


async def finalize_success(
    *,
    client: AsyncZammadClient,
    ctx: TicketJobContext,
    trigger_tag: str,
    now: datetime,
    storage_result: StorageResult,
) -> None:
    """Apply post-storage effects only after the archive write succeeded."""
    try:
        await async_retry(
            lambda: apply_done(client, ctx.ticket_id, trigger_tag=trigger_tag),
            max_retries=3,
            backoff_base=0.5,
            backoff_factor=2.0,
        )
    except Exception:  # pylint: disable=broad-exception-caught
        # The PDF and audit sidecar are durable at this point, but the ticket
        # state is not. Re-raise so the existing failure path can remove the
        # processing tag, record a failure, and avoid a false processed signal.
        log.exception(
            "process_ticket.finalization_failed_after_storage",
            ticket_id=ctx.ticket_id,
            storage_succeeded=True,
            storage_path=str(storage_result.target_path),
            sidecar_path=str(storage_result.sidecar_path),
            size_bytes=storage_result.size_bytes,
            sha256_hex=storage_result.sha256_hex,
        )
        raise

    if ctx.settings.workflow.acknowledge_on_success:
        note = _success_note(ctx, storage_result=storage_result, now=now)
        try:
            await client.create_internal_article(
                ctx.ticket_id,
                note.subject,
                note.html,
            )
        except asyncio.CancelledError as exc:
            _record_success_note_warning(ctx, storage_result=storage_result, cancelled=True)
            _record_success(ctx, storage_result=storage_result)
            raise CompletedTicketCancellation from exc
        except Exception:  # pylint: disable=broad-exception-caught
            # The archive and terminal tags are already durable. A best-effort
            # acknowledgement must not convert that success back into an error.
            _record_success_note_warning(ctx, storage_result=storage_result, cancelled=False)

    _record_success(ctx, storage_result=storage_result)


def _success_note(
    ctx: TicketJobContext,
    *,
    storage_result: StorageResult,
    now: datetime,
) -> _SuccessNote:
    return _SuccessNote(
        subject=f"PDF archived ({VERSION})",
        html=success_note_html(
            SuccessNotePayload(
                storage_dir=str(storage_result.target_path.parent),
                filename=storage_result.target_path.name,
                sidecar_path=str(storage_result.sidecar_path),
                size_bytes=storage_result.size_bytes,
                sha256_hex=storage_result.sha256_hex,
                request_id=ctx.request_id,
                delivery_id=ctx.delivery_id,
                timestamp_utc=format_timestamp_utc(now),
            )
        ),
    )


def _record_success_note_warning(
    ctx: TicketJobContext,
    *,
    storage_result: StorageResult,
    cancelled: bool,
) -> None:
    log_method = log.warning if cancelled else log.exception
    log_method(
        "process_ticket.success_note_cancelled_after_completion"
        if cancelled
        else "process_ticket.success_note_failed_after_completion",
        ticket_id=ctx.ticket_id,
        request_id=ctx.request_id,
        delivery_id=ctx.delivery_id,
        storage_path=str(storage_result.target_path),
    )
    record_history(
        ctx,
        status="processed_with_warning",
        classification="Warning",
        message="Archive completed, but the success note could not be posted",
    )


def _record_success(ctx: TicketJobContext, *, storage_result: StorageResult) -> None:
    processed_total.inc()
    record_history(ctx, status="processed")
    log.info(
        "process_ticket.done",
        ticket_id=ctx.ticket_id,
        storage_path=str(storage_result.target_path),
        request_id=ctx.request_id,
        delivery_id=ctx.delivery_id,
    )


async def cleanup_cancelled_pipeline(
    *,
    client: AsyncZammadClient,
    ctx: TicketJobContext,
    trigger_tag: str,
) -> None:
    """Release pipeline resources and record cancellation without retrying."""
    cleanup = asyncio.create_task(
        apply_error(
            client,
            ctx.ticket_id,
            keep_trigger=True,
            trigger_tag=trigger_tag,
        )
    )
    cleanup_succeeded = False
    try:
        await asyncio.wait_for(
            asyncio.shield(cleanup),
            timeout=_CANCELLATION_CLEANUP_TIMEOUT_SECONDS,
        )
        cleanup_succeeded = True
    except TimeoutError:
        cleanup.cancel()
        await asyncio.gather(cleanup, return_exceptions=True)
        log.error(
            "process_ticket.cancellation_cleanup_timed_out",
            ticket_id=ctx.ticket_id,
            request_id=ctx.request_id,
            delivery_id=ctx.delivery_id,
        )
    except Exception:  # pylint: disable=broad-exception-caught
        log.exception(
            "process_ticket.cancellation_cleanup_failed",
            ticket_id=ctx.ticket_id,
            request_id=ctx.request_id,
            delivery_id=ctx.delivery_id,
        )
    record_history(
        ctx,
        status="cancelled",
        classification="Transient",
        message=(
            "Processing cancelled; trigger restored for retry"
            if cleanup_succeeded
            else "Processing cancelled; tag cleanup did not complete"
        ),
    )


async def skip_not_triggered(
    ctx: TicketJobContext,
    *,
    tags: list[str],
) -> ProcessTicketResult:
    """Record why a ticket was intentionally excluded from archival."""
    log.info(
        "process_ticket.skip_should_not_process",
        ticket_id=ctx.ticket_id,
        tags=tags,
    )
    skipped_total.labels(reason="not_triggered").inc()
    record_history(ctx, status="skipped_not_triggered")
    return ProcessTicketResult(
        status="skipped_not_triggered",
        ticket_id=ctx.ticket_id,
    )
