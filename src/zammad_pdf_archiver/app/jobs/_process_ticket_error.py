from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from zammad_pdf_archiver._version import VERSION
from zammad_pdf_archiver.adapters.zammad.client import AsyncZammadClient
from zammad_pdf_archiver.app.jobs._process_ticket_models import (
    ProcessTicketResult,
    _PipelineErrorDetails,
    _TicketJobContext,
)
from zammad_pdf_archiver.app.jobs._ticket_notes import (
    action_hint,
    concise_exc_message,
    error_code_and_hint,
    error_note_html,
)
from zammad_pdf_archiver.app.jobs.retry_policy import classify
from zammad_pdf_archiver.domain.errors import PermanentError, TransientError
from zammad_pdf_archiver.domain.time_utils import format_timestamp_utc

RecordHistory = Callable[..., Any]
ApplyErrorWithRetry = Callable[..., Any]


@dataclass(frozen=True)
class ErrorDependencies:
    apply_error_with_retry: ApplyErrorWithRetry
    record_history: RecordHistory
    log: Any
    failed_total: Any


async def handle_ticket_pipeline_exception(
    *,
    client: AsyncZammadClient,
    ctx: _TicketJobContext,
    trigger_tag: str,
    exc: Exception,
    deps: ErrorDependencies,
) -> ProcessTicketResult:
    """Classify the exception, post an error note to the ticket, and update tags."""
    deps.failed_total.inc()
    details = pipeline_error_details(exc)
    log_pipeline_exception(ctx, details=details, log=deps.log)

    error_note_posted = await post_error_note(
        client=client,
        ctx=ctx,
        classification_label=details.classification_label,
        msg=details.message,
        action=details.action,
        code=details.code,
        hint=details.hint,
        log=deps.log,
    )
    error_tag_applied = await apply_error_and_cleanup_processing_tag(
        client=client,
        ctx=ctx,
        classification_label=details.classification_label,
        classified=details.classified,
        trigger_tag=trigger_tag,
        deps=deps,
    )
    log_incomplete_failure_visibility(
        ctx,
        details=details,
        error_note_posted=error_note_posted,
        error_tag_applied=error_tag_applied,
        log=deps.log,
    )

    history_recorded = await deps.record_history(
        ctx,
        status=details.status,
        classification=details.classification_label,
        message=details.message,
    )
    return pipeline_error_result(
        ctx,
        details=details,
        history_recorded=history_recorded,
        error_note_posted=error_note_posted,
        error_tag_applied=error_tag_applied,
    )


def log_pipeline_exception(
    ctx: _TicketJobContext, *, details: _PipelineErrorDetails, log: Any
) -> None:
    log.exception(
        "process_ticket.error",
        ticket_id=ctx.ticket_id,
        request_id=ctx.request_id,
        delivery_id=ctx.delivery_id,
        classification=details.classification_label,
        code=details.code or None,
        hint=details.hint or None,
    )


def pipeline_error_details(exc: Exception) -> _PipelineErrorDetails:
    classified = classify(exc)
    is_transient = isinstance(classified, TransientError)
    code, hint = error_code_and_hint(exc) if isinstance(classified, PermanentError) else ("", "")
    return _PipelineErrorDetails(
        classified=classified,
        classification_label="Transient" if is_transient else "Permanent",
        message=concise_exc_message(exc),
        action=action_hint(exc, classified=classified),
        code=code,
        hint=hint,
        status="failed_transient" if is_transient else "failed_permanent",
    )


def log_incomplete_failure_visibility(
    ctx: _TicketJobContext,
    *,
    details: _PipelineErrorDetails,
    error_note_posted: bool,
    error_tag_applied: bool,
    log: Any,
) -> None:
    if error_note_posted and error_tag_applied:
        return

    log.warning(
        "process_ticket.failure_visibility_incomplete",
        ticket_id=ctx.ticket_id,
        request_id=ctx.request_id,
        delivery_id=ctx.delivery_id,
        classification=details.classification_label,
        error_note_posted=error_note_posted,
        error_tag_applied=error_tag_applied,
    )


def pipeline_error_result(
    ctx: _TicketJobContext,
    *,
    details: _PipelineErrorDetails,
    history_recorded: bool,
    error_note_posted: bool,
    error_tag_applied: bool,
) -> ProcessTicketResult:
    return ProcessTicketResult(
        status=details.status,
        ticket_id=ctx.ticket_id,
        classification=details.classification_label,
        message=details.message,
        history_recorded=history_recorded,
        error_note_posted=error_note_posted,
        error_tag_applied=error_tag_applied,
    )


async def post_error_note(
    *,
    client: AsyncZammadClient,
    ctx: _TicketJobContext,
    classification_label: str,
    msg: str,
    action: str,
    code: str,
    hint: str,
    log: Any,
) -> bool:
    now = datetime.now(UTC)
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
        return True
    except Exception:
        log.exception(
            "process_ticket.error_note_failed",
            ticket_id=ctx.ticket_id,
            request_id=ctx.request_id,
            delivery_id=ctx.delivery_id,
            classification=classification_label,
        )
        return False


async def apply_error_and_cleanup_processing_tag(
    *,
    client: AsyncZammadClient,
    ctx: _TicketJobContext,
    classification_label: str,
    classified: TransientError | PermanentError | None,
    trigger_tag: str,
    deps: ErrorDependencies,
) -> bool:
    try:
        keep_trigger = isinstance(classified, TransientError)
        await deps.apply_error_with_retry(
            client,
            ticket_id=ctx.ticket_id,
            keep_trigger=keep_trigger,
            trigger_tag=trigger_tag,
        )
        return True
    except Exception:
        deps.log.exception(
            "process_ticket.apply_error_failed",
            ticket_id=ctx.ticket_id,
            request_id=ctx.request_id,
            delivery_id=ctx.delivery_id,
            classification=classification_label,
        )
        return False
