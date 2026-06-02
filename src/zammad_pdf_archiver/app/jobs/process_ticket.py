from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any

import structlog

from zammad_pdf_archiver._version import VERSION
from zammad_pdf_archiver.adapters.storage.layout import (
    build_filename_from_pattern,
    build_target_dir,
)
from zammad_pdf_archiver.adapters.zammad.client import (
    AsyncZammadClient,
    ZammadClientTransportOptions,
)
from zammad_pdf_archiver.adapters.zammad.models import TagList, Ticket
from zammad_pdf_archiver.app.constants import FORCE_REPROCESS_KEY, REQUEST_ID_KEY
from zammad_pdf_archiver.app.jobs import _process_ticket_error, _process_ticket_success
from zammad_pdf_archiver.app.jobs._process_ticket_models import (
    SKIPPED_PROCESS_STATUSES,
    ProcessTicketResult,
    ProcessTicketStatus,
    _ArchiveOutcome,
    _TicketJobContext,
)
from zammad_pdf_archiver.app.jobs._ticket_path import (
    determine_username,
    parse_archive_path_segments,
)
from zammad_pdf_archiver.app.jobs._ticket_renderer import build_and_render_pdf
from zammad_pdf_archiver.app.jobs.history import record_history_event
from zammad_pdf_archiver.app.jobs.ticket_storage import store_ticket_files
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
from zammad_pdf_archiver.domain.ticket_utils import ticket_custom_fields
from zammad_pdf_archiver.observability.metrics import (
    failed_total,
    processed_partial_total,
    processed_total,
    skipped_total,
    total_seconds,
)

log = structlog.get_logger(__name__)

__all__ = ["ProcessTicketResult", "ProcessTicketStatus", "VERSION", "process_ticket"]


async def _record_history(
    ctx: _TicketJobContext,
    *,
    status: str,
    classification: str | None = None,
    message: str = "",
) -> bool:
    try:
        return await record_history_event(
            ctx.settings,
            status=status,
            ticket_id=ctx.ticket_id,
            classification=classification,
            message=message,
            delivery_id=ctx.delivery_id,
            request_id=ctx.request_id,
        )
    except Exception:
        log.warning("process_ticket.history_record_failed", status=status, ticket_id=ctx.ticket_id)
        return False


async def _apply_done_with_backoff(
    client: AsyncZammadClient,
    *,
    ticket_id: int,
    trigger_tag: str,
) -> None:
    last_exc: Exception | None = None
    for delay in (0.5, 1.0, 2.0, None):
        try:
            await apply_done(client, ticket_id, trigger_tag=trigger_tag)
            return
        except PermanentError:
            raise
        except Exception as exc:
            last_exc = exc
            if delay is not None:
                await asyncio.sleep(delay)
    if last_exc is None:
        raise RuntimeError("apply_done retry loop exited without an exception")
    raise last_exc


async def _apply_error_with_retry(
    client: AsyncZammadClient,
    *,
    ticket_id: int,
    keep_trigger: bool,
    trigger_tag: str,
) -> None:
    # Intentional asymmetry with _apply_done_with_backoff: success finalization
    # gets the longer retry window because archive bytes are already stored and
    # Zammad's done tag is the operator-visible acknowledgement. Error tagging
    # gets one short retry; if it still fails, the error note/result/logs carry
    # the failure and a stale processing tag is safer than reporting success.
    try:
        await apply_error(
            client,
            ticket_id,
            keep_trigger=keep_trigger,
            trigger_tag=trigger_tag,
        )
    except Exception as first_exc:
        log.warning(
            "apply_error_first_attempt_failed",
            ticket_id=ticket_id,
            exc_info=first_exc,
        )
        await asyncio.sleep(0.3)
        await apply_error(
            client,
            ticket_id,
            keep_trigger=keep_trigger,
            trigger_tag=trigger_tag,
        )


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
        history_recorded = await _record_history(stub_ctx, status="skipped_no_ticket_id")
        return ProcessTicketResult(
            status="skipped_no_ticket_id",
            ticket_id=None,
            history_recorded=history_recorded,
        )

    request_id = (
        raw_request_id if isinstance(raw_request_id, str) and raw_request_id.strip() else None
    )
    ctx = _TicketJobContext(
        settings=settings,
        ticket_id=ticket_id,
        delivery_id=delivery_id,
        request_id=request_id,
    )

    bound: dict[str, object] = {"ticket_id": ctx.ticket_id}
    if ctx.delivery_id:
        bound["delivery_id"] = ctx.delivery_id
    if ctx.request_id:
        bound["request_id"] = ctx.request_id
    with structlog.contextvars.bound_contextvars(**bound):
        return await _process_with_ticket_lock(ctx, payload=payload)


async def _process_with_ticket_lock(
    ctx: _TicketJobContext,
    *,
    payload: dict[str, Any],
) -> ProcessTicketResult:
    """Acquire an exclusive lock for the ticket, then run the processing pipeline."""
    try:
        acquired = await try_acquire_ticket(ctx.settings, ctx.ticket_id)
    except TransientError as exc:
        failed_total.inc()
        log.warning(
            "process_ticket.ticket_lock_unavailable",
            ticket_id=ctx.ticket_id,
            delivery_id=ctx.delivery_id,
        )
        history_recorded = await _record_history(
            ctx,
            status="failed_transient",
            classification="Transient",
            message=str(exc),
        )
        return ProcessTicketResult(
            status="failed_transient",
            ticket_id=ctx.ticket_id,
            classification="Transient",
            message=str(exc),
            history_recorded=history_recorded,
        )
    if not acquired:
        return await _skip_in_flight(ctx)

    result: ProcessTicketResult | None = None
    try:
        claimed = await _claim_delivery_or_skip(ctx)
        if claimed is not None:
            result = claimed
        else:
            result = await _process_ticket_with_client(ctx, payload=payload)
    finally:
        lock_release_failed = await _release_ticket_lock(ctx)

    if result is None:  # pragma: no cover - defensive; exceptions exit through finally above.
        raise RuntimeError("process_ticket completed without a result")
    if lock_release_failed:
        return replace(result, lock_release_failed=True)
    return result


async def _skip_in_flight(ctx: _TicketJobContext) -> ProcessTicketResult:
    """Return a skip result when another worker is already processing this ticket."""
    log.info(
        "process_ticket.skip_ticket_in_flight",
        ticket_id=ctx.ticket_id,
        delivery_id=ctx.delivery_id,
    )
    skipped_total.labels(reason="in_flight").inc()
    history_recorded = await _record_history(ctx, status="skipped_in_flight")
    return ProcessTicketResult(
        status="skipped_in_flight",
        ticket_id=ctx.ticket_id,
        history_recorded=history_recorded,
    )


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
    history_recorded = await _record_history(ctx, status="skipped_idempotency")
    return ProcessTicketResult(
        status="skipped_idempotency",
        ticket_id=ctx.ticket_id,
        history_recorded=history_recorded,
    )


async def _process_ticket_with_client(
    ctx: _TicketJobContext,
    *,
    payload: dict[str, Any],
) -> ProcessTicketResult:
    """Open a Zammad client session and drive the full ticket archival pipeline."""
    settings = ctx.settings
    trigger_tag = str(settings.workflow.trigger_tag).strip() or TRIGGER_TAG
    require_trigger_tag = bool(settings.workflow.require_tag)
    force_reprocess = payload.get(FORCE_REPROCESS_KEY) is True

    async with AsyncZammadClient(
        base_url=str(settings.zammad.base_url),
        api_token=settings.zammad.api_token.get_secret_value(),
        transport=ZammadClientTransportOptions(
            timeout_seconds=settings.zammad.timeout_seconds,
            verify_tls=settings.zammad.verify_tls,
            trust_env=settings.hardening.transport.trust_env,
        ),
    ) as client:
        result: ProcessTicketResult | None = None
        total_start = perf_counter()
        try:
            result = await _run_ticket_pipeline(
                client=client,
                ctx=ctx,
                payload=payload,
                trigger_tag=trigger_tag,
                require_trigger_tag=require_trigger_tag,
                force_reprocess=force_reprocess,
            )
            return result
        except asyncio.CancelledError:
            # Cancellation during shutdown should not mutate ticket state.
            raise
        except Exception as exc:
            result = await _handle_ticket_pipeline_exception(
                client=client,
                ctx=ctx,
                trigger_tag=trigger_tag,
                exc=exc,
            )
            return result
        finally:
            if result is None or result.status not in SKIPPED_PROCESS_STATUSES:
                total_seconds.observe(perf_counter() - total_start)


async def _run_ticket_pipeline(
    *,
    client: AsyncZammadClient,
    ctx: _TicketJobContext,
    payload: dict[str, Any],
    trigger_tag: str,
    require_trigger_tag: bool,
    force_reprocess: bool,
) -> ProcessTicketResult:
    """Fetch ticket data, render PDF, store files, and acknowledge success."""
    settings = ctx.settings
    ticket = await client.get_ticket(ctx.ticket_id)
    tags = await client.list_tags(ctx.ticket_id)
    # IMPORTANT: should_process is a non-atomic tag check.  In multi-instance
    # deployments, a second worker may read the same tags before the first worker
    # writes PROCESSING_TAG.  Multi-instance deployments MUST use
    # idempotency_backend=redis and execution_backend=redis_queue to prevent
    # duplicate processing.  See state_machine.py for details.
    if not force_reprocess and not should_process(
        tags.root,
        trigger_tag=trigger_tag,
        require_trigger_tag=require_trigger_tag,
    ):
        return await _skip_not_triggered(ctx, tags=tags.root)

    await apply_processing(
        client,
        ctx.ticket_id,
        trigger_tag=trigger_tag,
        force_reprocess=force_reprocess,
    )

    now = datetime.now(UTC)
    target_path = _target_path_for_ticket(
        ticket=ticket,
        payload=payload,
        settings=settings,
        now=now,
    )
    outcome = await _render_and_store_archive(
        client=client,
        ctx=ctx,
        ticket=ticket,
        tags=tags,
        target_path=target_path,
        now=now,
    )
    return await _finalize_successful_archive(
        client=client,
        ctx=ctx,
        trigger_tag=trigger_tag,
        outcome=outcome,
    )


def _target_path_for_ticket(
    *,
    ticket: Ticket,
    payload: dict[str, Any],
    settings: Settings,
    now: datetime,
) -> Path:
    custom_fields = ticket_custom_fields(ticket)
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
        allow_prefixes=settings.storage.path_policy.allow_prefixes,
    )
    filename = build_filename_from_pattern(
        settings.storage.path_policy.filename_pattern,
        ticket_number=ticket.number,
        timestamp_utc=now.date().isoformat(),
    )
    return target_dir / filename


async def _render_and_store_archive(
    *,
    client: AsyncZammadClient,
    ctx: _TicketJobContext,
    ticket: Ticket,
    tags: TagList,
    target_path: Path,
    now: datetime,
) -> _ArchiveOutcome:
    settings = ctx.settings
    pdf_bytes, snapshot, articles_capped, attachments_skipped = await build_and_render_pdf(
        client,
        ticket,
        tags,
        ctx.ticket_id,
        settings,
    )
    storage_result = store_ticket_files(
        pdf_bytes=pdf_bytes,
        snapshot=snapshot,
        target_path=target_path,
        ticket_id=ticket.id,
        now=now,
        settings=settings,
    )
    outcome = _ArchiveOutcome(
        storage_result=storage_result,
        articles_capped=articles_capped,
        attachments_skipped=attachments_skipped,
        now=now,
    )
    return outcome


async def _finalize_successful_archive(
    *,
    client: AsyncZammadClient,
    ctx: _TicketJobContext,
    trigger_tag: str,
    outcome: _ArchiveOutcome,
) -> ProcessTicketResult:
    return await _process_ticket_success.finalize_successful_archive(
        client=client,
        ctx=ctx,
        trigger_tag=trigger_tag,
        outcome=outcome,
        deps=_process_ticket_success.SuccessDependencies(
            apply_done_with_backoff=_apply_done_with_backoff,
            record_history=_record_history,
            log=log,
            failed_total=failed_total,
            processed_partial_total=processed_partial_total,
            processed_total=processed_total,
        ),
    )


async def _skip_not_triggered(
    ctx: _TicketJobContext,
    *,
    tags: list[str],
) -> ProcessTicketResult:
    """Return a skip result when the ticket lacks the required trigger tag."""
    log.info(
        "process_ticket.skip_should_not_process",
        ticket_id=ctx.ticket_id,
        tags=tags,
    )
    skipped_total.labels(reason="not_triggered").inc()
    history_recorded = await _record_history(ctx, status="skipped_not_triggered")
    return ProcessTicketResult(
        status="skipped_not_triggered",
        ticket_id=ctx.ticket_id,
        history_recorded=history_recorded,
    )


async def _handle_ticket_pipeline_exception(
    *,
    client: AsyncZammadClient,
    ctx: _TicketJobContext,
    trigger_tag: str,
    exc: Exception,
) -> ProcessTicketResult:
    return await _process_ticket_error.handle_ticket_pipeline_exception(
        client=client,
        ctx=ctx,
        trigger_tag=trigger_tag,
        exc=exc,
        deps=_process_ticket_error.ErrorDependencies(
            apply_error_with_retry=_apply_error_with_retry,
            record_history=_record_history,
            log=log,
            failed_total=failed_total,
        ),
    )


async def _release_ticket_lock(ctx: _TicketJobContext) -> bool:
    try:
        return not await asyncio.shield(release_ticket(ctx.settings, ctx.ticket_id))
    except Exception:
        log.exception(
            "process_ticket.release_ticket_failed",
            ticket_id=ctx.ticket_id,
            request_id=ctx.request_id,
            delivery_id=ctx.delivery_id,
        )
        return True
