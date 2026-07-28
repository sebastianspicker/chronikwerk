"""Internal failure handling for the ticket processing pipeline."""

from __future__ import annotations

import structlog

from chronikwerk._version import VERSION
from chronikwerk.adapters.zammad.client import AsyncZammadClient
from chronikwerk.app.jobs import _ticket_pipeline
from chronikwerk.app.jobs.async_retry import async_retry
from chronikwerk.app.jobs.retry_policy import classify
from chronikwerk.app.jobs.ticket_notes import (
    action_hint,
    concise_exc_message,
    error_code_and_hint,
    error_note_html,
)
from chronikwerk.domain.errors import PermanentError, TransientError
from chronikwerk.domain.state_machine import apply_error
from chronikwerk.domain.time_utils import format_timestamp_utc, now_utc
from chronikwerk.observability.metrics import failed_total

log = structlog.get_logger(__name__)


async def handle_ticket_pipeline_exception(
    *,
    client: AsyncZammadClient,
    ctx: _ticket_pipeline.TicketJobContext,
    trigger_tag: str,
    exc: Exception,
) -> _ticket_pipeline.ProcessTicketResult:
    """Classify an exception, post an error note, and update terminal tags."""
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
    _ticket_pipeline.record_history(
        ctx,
        status=status,
        classification=classification_label,
        message=msg,
    )
    return _ticket_pipeline.ProcessTicketResult(
        status=status,
        ticket_id=ctx.ticket_id,
        classification=classification_label,
        message=msg,
    )


def _log_pipeline_error(
    ctx: _ticket_pipeline.TicketJobContext,
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
    ctx: _ticket_pipeline.TicketJobContext,
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
    ctx: _ticket_pipeline.TicketJobContext,
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
