"""Project module."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from time import perf_counter
from typing import Any

import structlog

from zammad_pdf_archiver._version import VERSION
from zammad_pdf_archiver.adapters.storage.layout import (
    build_filename_from_pattern,
    build_target_dir,
)
from zammad_pdf_archiver.adapters.zammad.client import AsyncZammadClient
from zammad_pdf_archiver.app.constants import FORCE_REPROCESS_KEY, REQUEST_ID_KEY
from zammad_pdf_archiver.app.jobs.async_retry import async_retry
from zammad_pdf_archiver.app.jobs.history import record_history_event
from zammad_pdf_archiver.app.jobs.retry_policy import classify
from zammad_pdf_archiver.app.jobs.ticket_notes import (
    action_hint,
    concise_exc_message,
    error_code_and_hint,
    error_note_html,
    success_note_html,
)
from zammad_pdf_archiver.app.jobs.ticket_path import (
    determine_username,
    parse_archive_path_segments,
)
from zammad_pdf_archiver.app.jobs.ticket_renderer import build_and_render_pdf
from zammad_pdf_archiver.app.jobs.ticket_storage import (
    StorageResult,
    store_ticket_files,
)
from zammad_pdf_archiver.app.jobs.ticket_stores import (
    release_ticket,
    try_acquire_ticket,
    try_claim_delivery_id,
)
from zammad_pdf_archiver.config.settings import Settings
from zammad_pdf_archiver.domain.errors import PermanentError, TransientError
from zammad_pdf_archiver.domain.state_machine import (
    TRIGGER_TAG,
    apply_done,
    apply_error,
    apply_processing,
    should_process,
)
from zammad_pdf_archiver.domain.ticket_id import extract_ticket_id
from zammad_pdf_archiver.domain.time_utils import format_timestamp_utc, now_utc
from zammad_pdf_archiver.observability.metrics import (
    failed_total,
    processed_total,
    skipped_total,
    total_seconds,
)

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class _TicketJobContext:
    """Common context threaded through the ticket processing pipeline."""

    settings: Settings
    ticket_id: int
    delivery_id: str | None
    request_id: str | None


@dataclass(frozen=True)
class ProcessTicketResult:
    """Implement the ProcessTicketResult operation."""
    status: str
    ticket_id: int | None
    classification: str | None = None
    message: str = ""


def _record_history(
    ctx: _TicketJobContext,
    *,
    status: str,
    classification: str | None = None,
    message: str = "",
) -> None:
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


async def process_ticket(
    delivery_id: str | None,
    payload: dict[str, Any],
    settings: Settings,
) -> ProcessTicketResult:
    """Orchestrate the full ticket archival pipeline for a single ingest payload."""
    raw_request_id = payload.get(REQUEST_ID_KEY)
    ticket_id = extract_ticket_id(payload)
    if ticket_id is None:
        request_id = raw_request_id if isinstance(raw_request_id, str) else None
        log.info("process_ticket.skip_no_ticket_id", request_id=request_id)
        skipped_total.labels(reason="no_ticket_id").inc()
        stub_ctx = _TicketJobContext(
            settings=settings,
            ticket_id=0,
            delivery_id=delivery_id,
            request_id=request_id,
        )
        _record_history(stub_ctx, status="skipped_no_ticket_id")
        return ProcessTicketResult(status="skipped_no_ticket_id", ticket_id=None)

    request_id = (
        raw_request_id if isinstance(raw_request_id, str) and raw_request_id.strip() else None
    )
    ctx = _TicketJobContext(
        settings=settings,
        ticket_id=ticket_id,
        delivery_id=delivery_id,
        request_id=request_id,
    )

    bound = _bound_context(ctx)
    with structlog.contextvars.bound_contextvars(**bound):
        return await _process_with_ticket_lock(ctx, payload=payload)


def _bound_context(ctx: _TicketJobContext) -> dict[str, object]:
    bound: dict[str, object] = {"ticket_id": ctx.ticket_id}
    if ctx.delivery_id:
        bound["delivery_id"] = ctx.delivery_id
    if ctx.request_id:
        bound["request_id"] = ctx.request_id
    return bound


def _force_reprocess_requested(payload: dict[str, Any]) -> bool:
    return payload.get(FORCE_REPROCESS_KEY) is True


async def _process_with_ticket_lock(
    ctx: _TicketJobContext,
    *,
    payload: dict[str, Any],
) -> ProcessTicketResult:
    acquired = await try_acquire_ticket(ctx.settings, ctx.ticket_id)
    if not acquired:
        return await _skip_in_flight(ctx)

    try:
        claimed = await _claim_delivery_or_skip(ctx)
        if claimed is not None:
            return claimed
        return await _process_ticket_with_client(ctx, payload=payload)
    finally:
        await _release_ticket_lock(ctx)


async def _skip_in_flight(ctx: _TicketJobContext) -> ProcessTicketResult:
    """Return a skip result when another worker is already processing this ticket."""
    log.info(
        "process_ticket.skip_ticket_in_flight",
        ticket_id=ctx.ticket_id,
        delivery_id=ctx.delivery_id,
    )
    skipped_total.labels(reason="in_flight").inc()
    _record_history(ctx, status="skipped_in_flight")
    return ProcessTicketResult(status="skipped_in_flight", ticket_id=ctx.ticket_id)


async def _claim_delivery_or_skip(ctx: _TicketJobContext) -> ProcessTicketResult | None:
    """Enforce at-most-once delivery; returns a skip result if this delivery was already seen."""
    if not ctx.delivery_id:
        return None
    if await try_claim_delivery_id(ctx.settings, ctx.delivery_id):
        return None

    log.info(
        "process_ticket.skip_delivery_id_seen",
        ticket_id=ctx.ticket_id,
        delivery_id=ctx.delivery_id,
    )
    skipped_total.labels(reason="idempotency").inc()
    _record_history(ctx, status="skipped_idempotency")
    return ProcessTicketResult(status="skipped_idempotency", ticket_id=ctx.ticket_id)


async def _process_ticket_with_client(
    ctx: _TicketJobContext,
    *,
    payload: dict[str, Any],
) -> ProcessTicketResult:
    """Open a Zammad client session and drive the full ticket archival pipeline."""
    settings = ctx.settings
    trigger_tag = str(settings.workflow.trigger_tag).strip() or TRIGGER_TAG
    require_trigger_tag = bool(settings.workflow.require_tag)
    force_reprocess = _force_reprocess_requested(payload)

    async with AsyncZammadClient(
        base_url=str(settings.zammad.base_url),
        api_token=settings.zammad.api_token.get_secret_value(),
        timeout_seconds=settings.zammad.timeout_seconds,
        verify_tls=settings.zammad.verify_tls,
        trust_env=settings.hardening.transport.trust_env,
        allow_insecure_http=settings.hardening.transport.allow_insecure_http,
        allow_private_networks=settings.hardening.transport.allow_private_networks,
    ) as client:
        observe_total = True
        total_start = perf_counter()
        try:
            result, observe_total = await _run_ticket_pipeline(
                client=client,
                ctx=ctx,
                payload=payload,
                trigger_tag=trigger_tag,
                require_trigger_tag=require_trigger_tag,
                force_reprocess=force_reprocess,
            )
            return result
        except Exception as exc:  # pylint: disable=broad-exception-caught
            return await _handle_ticket_pipeline_exception(
                client=client,
                ctx=ctx,
                trigger_tag=trigger_tag,
                exc=exc,
            )
        finally:
            if observe_total:
                total_seconds.observe(perf_counter() - total_start)


async def _run_ticket_pipeline(
    *,
    client: AsyncZammadClient,
    ctx: _TicketJobContext,
    payload: dict[str, Any],
    trigger_tag: str,
    require_trigger_tag: bool,
    force_reprocess: bool,
) -> tuple[ProcessTicketResult, bool]:
    """Fetch ticket data, render PDF, store files, and acknowledge success.

    Returns the result and whether total-time metrics should be observed.
    """
    ticket = await client.get_ticket(ctx.ticket_id)
    tags = await client.list_tags(ctx.ticket_id)
    # IMPORTANT: should_process is a non-atomic tag check.  In multi-instance
    # deployments, a second worker may read the same tags before the first worker
    # writes PROCESSING_TAG.  Multi-instance deployments MUST use
    # duplicate processing.  See state_machine.py for details.
    if not force_reprocess and not should_process(
        tags.root,
        trigger_tag=trigger_tag,
        require_trigger_tag=require_trigger_tag,
    ):
        return (
            await _skip_not_triggered(ctx, tags=tags.root),
            False,
        )

    await apply_processing(
        client,
        ctx.ticket_id,
        trigger_tag=trigger_tag,
        force_reprocess=force_reprocess,
    )

    now = now_utc()
    storage_paths = _resolve_storage_paths(ctx, ticket=ticket, payload=payload, now=now)
    storage_result = await _render_and_store_ticket(
        client=client,
        ctx=ctx,
        ticket=ticket,
        tags=tags,
        storage_paths=storage_paths,
        now=now,
    )
    await _finalize_success(
        client=client,
        ctx=ctx,
        trigger_tag=trigger_tag,
        now=now,
        storage_result=storage_result,
    )
    return ProcessTicketResult(status="processed", ticket_id=ctx.ticket_id), True


def _resolve_storage_paths(
    ctx: _TicketJobContext,
    *,
    ticket,
    payload: dict[str, Any],
    now: datetime,
) -> tuple[Path, Path]:
    settings = ctx.settings
    custom_fields = (
        ticket.preferences.custom_fields
        if ticket.preferences is not None
        and isinstance(ticket.preferences.custom_fields, dict)
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


async def _render_and_store_ticket(
    *,
    client: AsyncZammadClient,
    ctx: _TicketJobContext,
    ticket,
    tags,
    storage_paths: tuple[Path, Path],
    now: datetime,
) -> StorageResult:
    pdf_bytes, snapshot = await build_and_render_pdf(
        client=client,
        ticket=ticket,
        tags=tags,
        ticket_id=ctx.ticket_id,
        settings=ctx.settings,
    )
    target_path, sidecar_path = storage_paths
    return await asyncio.to_thread(
        store_ticket_files,
        pdf_bytes=pdf_bytes,
        snapshot=snapshot,
        target_path=target_path,
        sidecar_path=sidecar_path,
        ticket_id=ticket.id,
        now=now,
        settings=ctx.settings,
    )


async def _finalize_success(
    *,
    client: AsyncZammadClient,
    ctx: _TicketJobContext,
    trigger_tag: str,
    now: datetime,
    storage_result: StorageResult,
) -> None:
    try:
        await async_retry(
            lambda: apply_done(client, ctx.ticket_id, trigger_tag=trigger_tag),
            max_retries=3,
            backoff_base=0.5,
            backoff_factor=2.0,
        )
    except Exception:  # pylint: disable=broad-exception-caught
        # The PDF and audit sidecar are durable at this point, but the ticket
        # state is not.  Re-raise so the existing failure path can remove the
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
        await client.create_internal_article(
            ctx.ticket_id,
            f"PDF archived ({VERSION})",
            success_note_html(
                storage_dir=str(storage_result.target_path.parent),
                filename=storage_result.target_path.name,
                sidecar_path=str(storage_result.sidecar_path),
                size_bytes=storage_result.size_bytes,
                sha256_hex=storage_result.sha256_hex,
                request_id=ctx.request_id,
                delivery_id=ctx.delivery_id,
                timestamp_utc=format_timestamp_utc(now),
            ),
        )

    processed_total.inc()
    _record_history(ctx, status="processed")
    log.info(
        "process_ticket.done",
        ticket_id=ctx.ticket_id,
        storage_path=str(storage_result.target_path),
        request_id=ctx.request_id,
        delivery_id=ctx.delivery_id,
    )


async def _skip_not_triggered(
    ctx: _TicketJobContext,
    *,
    tags: list[str],
) -> ProcessTicketResult:
    log.info(
        "process_ticket.skip_should_not_process",
        ticket_id=ctx.ticket_id,
        tags=tags,
    )
    skipped_total.labels(reason="not_triggered").inc()
    _record_history(ctx, status="skipped_not_triggered")
    return ProcessTicketResult(
        status="skipped_not_triggered",
        ticket_id=ctx.ticket_id,
    )


async def _handle_ticket_pipeline_exception(
    *,
    client: AsyncZammadClient,
    ctx: _TicketJobContext,
    trigger_tag: str,
    exc: Exception,
) -> ProcessTicketResult:
    """Classify the exception, post an error note to the ticket, and update tags."""
    failed_total.inc()
    classified = classify(exc)
    classification_label = _classification_label(classified)
    msg = concise_exc_message(exc)
    action = action_hint(exc, classified=classified) if classified is not None else ""
    code, hint = _error_code_hint(exc, classified=classified)

    _log_pipeline_error(ctx, classification_label=classification_label, code=code, hint=hint)

    await _post_error_note(
        client=client,
        ctx=ctx,
        classification_label=classification_label,
        msg=msg,
        action=action,
        code=code,
        hint=hint,
    )
    await _apply_error_and_cleanup_processing_tag(
        client=client,
        ctx=ctx,
        classification_label=classification_label,
        classified=classified,
        trigger_tag=trigger_tag,
    )

    status = _failure_status(classified)
    _record_history(
        ctx,
        status=status,
        classification=classification_label,
        message=msg,
    )
    return ProcessTicketResult(
        status=status,
        ticket_id=ctx.ticket_id,
        classification=classification_label,
        message=msg,
    )


def _log_pipeline_error(
    ctx: _TicketJobContext,
    *,
    classification_label: str,
    code: str,
    hint: str,
) -> None:
    log.exception(
        "process_ticket.error",
        ticket_id=ctx.ticket_id,
        request_id=ctx.request_id,
        delivery_id=ctx.delivery_id,
        classification=classification_label,
        code=code or None,
        hint=hint or None,
    )


def _failure_status(classified: TransientError | PermanentError | None) -> str:
    if classified is not None and isinstance(classified, TransientError):
        return "failed_transient"
    return "failed_permanent"


def _classification_label(classified: TransientError | PermanentError | None) -> str:
    """Map a classified error to its human-readable label for notes and metrics."""
    is_transient = classified is not None and isinstance(classified, TransientError)
    return "Transient" if is_transient else "Permanent"


def _error_code_hint(
    exc: BaseException, *, classified: TransientError | PermanentError | None
) -> tuple[str, str]:
    """Extract a structured error code and hint, but only for permanent errors."""
    if classified is not None and isinstance(classified, PermanentError):
        return error_code_and_hint(exc)
    return "", ""


async def _post_error_note(
    *,
    client: AsyncZammadClient,
    ctx: _TicketJobContext,
    classification_label: str,
    msg: str,
    action: str,
    code: str,
    hint: str,
) -> None:
    now = now_utc()
    try:
        await client.create_internal_article(
            ctx.ticket_id,
            f"PDF archiver error ({VERSION})",
            error_note_html(
                classification=classification_label,
                message=msg,
                action=action,
                request_id=ctx.request_id,
                delivery_id=ctx.delivery_id,
                timestamp_utc=format_timestamp_utc(now),
                code=code,
                hint=hint,
            ),
        )
    except Exception:  # pylint: disable=broad-exception-caught
        log.exception(
            "process_ticket.error_note_failed",
            ticket_id=ctx.ticket_id,
            request_id=ctx.request_id,
            delivery_id=ctx.delivery_id,
            classification=classification_label,
        )


async def _apply_error_and_cleanup_processing_tag(
    *,
    client: AsyncZammadClient,
    ctx: _TicketJobContext,
    classification_label: str,
    classified: TransientError | PermanentError | None,
    trigger_tag: str,
) -> None:
    try:
        keep_trigger = classified is not None and isinstance(classified, TransientError)
        await async_retry(
            lambda: apply_error(
                client,
                ctx.ticket_id,
                keep_trigger=keep_trigger,
                trigger_tag=trigger_tag,
            ),
            max_retries=1,
            backoff_base=0.3,
        )
    except Exception:  # pylint: disable=broad-exception-caught
        log.exception(
            "process_ticket.apply_error_failed",
            ticket_id=ctx.ticket_id,
            request_id=ctx.request_id,
            delivery_id=ctx.delivery_id,
            classification=classification_label,
        )
        return


async def _release_ticket_lock(ctx: _TicketJobContext) -> None:
    try:
        await asyncio.shield(release_ticket(ctx.ticket_id))
    except Exception:  # pylint: disable=broad-exception-caught
        log.exception(
            "process_ticket.release_ticket_failed",
            ticket_id=ctx.ticket_id,
            request_id=ctx.request_id,
            delivery_id=ctx.delivery_id,
        )
