from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from zammad_pdf_archiver._version import VERSION
from zammad_pdf_archiver.adapters.zammad.client import AsyncZammadClient
from zammad_pdf_archiver.app.jobs._process_ticket_models import (
    ProcessTicketResult,
    _ArchiveOutcome,
    _TicketJobContext,
)
from zammad_pdf_archiver.app.jobs._ticket_notes import error_note_html, success_note_html
from zammad_pdf_archiver.domain.time_utils import format_timestamp_utc

RecordHistory = Callable[..., Any]
ApplyDoneWithBackoff = Callable[..., Any]


@dataclass(frozen=True)
class SuccessDependencies:
    apply_done_with_backoff: ApplyDoneWithBackoff
    record_history: RecordHistory
    log: Any
    failed_total: Any
    processed_partial_total: Any
    processed_total: Any


async def finalize_successful_archive(
    *,
    client: AsyncZammadClient,
    ctx: _TicketJobContext,
    trigger_tag: str,
    outcome: _ArchiveOutcome,
    deps: SuccessDependencies,
) -> ProcessTicketResult:
    if outcome.articles_capped or outcome.attachments_skipped > 0:
        deps.processed_partial_total.inc()
    done_applied = await try_apply_done_tag(
        client=client,
        ctx=ctx,
        trigger_tag=trigger_tag,
        deps=deps,
    )
    if not done_applied:
        return await handle_done_tag_update_failure(
            client=client,
            ctx=ctx,
            outcome=outcome,
            deps=deps,
        )

    return await finalize_after_done_applied(
        client=client,
        ctx=ctx,
        outcome=outcome,
        deps=deps,
    )


async def handle_done_tag_update_failure(
    *,
    client: AsyncZammadClient,
    ctx: _TicketJobContext,
    outcome: _ArchiveOutcome,
    deps: SuccessDependencies,
) -> ProcessTicketResult:
    message = "archive stored but final done tag update failed"
    deps.failed_total.inc()
    partial_note_posted = await post_done_tag_update_failure_note(
        client=client,
        ctx=ctx,
        outcome=outcome,
        message=message,
        log=deps.log,
    )
    history_recorded = await deps.record_history(
        ctx,
        status="processed_done_update_failed",
        classification="Partial",
        message=message,
    )
    deps.log.info(
        "process_ticket.done_update_failed",
        ticket_id=ctx.ticket_id,
        storage_path=str(outcome.storage_result.target_path),
        request_id=ctx.request_id,
        delivery_id=ctx.delivery_id,
    )
    return ProcessTicketResult(
        status="processed_done_update_failed",
        ticket_id=ctx.ticket_id,
        classification="Partial",
        message=message,
        articles_capped=outcome.articles_capped,
        attachments_skipped=outcome.attachments_skipped,
        history_recorded=history_recorded,
        error_note_posted=partial_note_posted,
    )


async def post_done_tag_update_failure_note(
    *,
    client: AsyncZammadClient,
    ctx: _TicketJobContext,
    outcome: _ArchiveOutcome,
    message: str,
    log: Any,
) -> bool:
    try:
        await client.create_internal_article(
            ctx.ticket_id,
            f"PDF archiver partial failure ({VERSION})",
            error_note_html(
                classification="Partial",
                message=message,
                action=(
                    "The PDF and audit sidecar were written, but the final pdf:signed "
                    "tag update failed. Review service logs and retry the ticket after "
                    "confirming the stored archive."
                ),
                request_id=ctx.request_id,
                delivery_id=ctx.delivery_id,
                timestamp_utc=format_timestamp_utc(outcome.now),
                code="done_tag_update_failed",
                hint="Archive exists on disk; do not assume the Zammad tag state is final.",
            ),
        )
        return True
    except Exception:
        log.exception(
            "process_ticket.partial_done_note_failed",
            ticket_id=ctx.ticket_id,
            storage_path=str(outcome.storage_result.target_path),
            request_id=ctx.request_id,
            delivery_id=ctx.delivery_id,
        )
        return False


async def handle_success_acknowledgement_failure(
    ctx: _TicketJobContext,
    *,
    outcome: _ArchiveOutcome,
    deps: SuccessDependencies,
) -> ProcessTicketResult:
    message = "archive finalized but success acknowledgement failed"
    deps.failed_total.inc()
    history_recorded = await deps.record_history(
        ctx,
        status="processed_acknowledgement_failed",
        classification="Partial",
        message=message,
    )
    deps.log.exception(
        "process_ticket.success_acknowledgement_failed",
        ticket_id=ctx.ticket_id,
        storage_path=str(outcome.storage_result.target_path),
        request_id=ctx.request_id,
        delivery_id=ctx.delivery_id,
    )
    return ProcessTicketResult(
        status="processed_acknowledgement_failed",
        ticket_id=ctx.ticket_id,
        classification="Partial",
        message=message,
        articles_capped=outcome.articles_capped,
        attachments_skipped=outcome.attachments_skipped,
        history_recorded=history_recorded,
    )


async def processed_archive_result(
    ctx: _TicketJobContext,
    *,
    outcome: _ArchiveOutcome,
    deps: SuccessDependencies,
) -> ProcessTicketResult:
    deps.processed_total.inc()
    history_recorded = await deps.record_history(ctx, status="processed")
    deps.log.info(
        "process_ticket.done",
        ticket_id=ctx.ticket_id,
        storage_path=str(outcome.storage_result.target_path),
        request_id=ctx.request_id,
        delivery_id=ctx.delivery_id,
    )
    return ProcessTicketResult(
        status="processed",
        ticket_id=ctx.ticket_id,
        articles_capped=outcome.articles_capped,
        attachments_skipped=outcome.attachments_skipped,
        history_recorded=history_recorded,
    )


async def try_apply_done_tag(
    *,
    client: AsyncZammadClient,
    ctx: _TicketJobContext,
    trigger_tag: str,
    deps: SuccessDependencies,
) -> bool:
    try:
        await deps.apply_done_with_backoff(
            client,
            ticket_id=ctx.ticket_id,
            trigger_tag=trigger_tag,
        )
        return True
    except Exception:
        deps.log.exception("process_ticket.apply_done_failed", ticket_id=ctx.ticket_id)
        return False


async def finalize_after_done_applied(
    client: AsyncZammadClient,
    ctx: _TicketJobContext,
    *,
    outcome: _ArchiveOutcome,
    deps: SuccessDependencies,
) -> ProcessTicketResult:
    try:
        await acknowledge_success_if_enabled(
            client=client,
            ctx=ctx,
            outcome=outcome,
        )
    except Exception:
        return await handle_success_acknowledgement_failure(ctx, outcome=outcome, deps=deps)
    return await processed_archive_result(ctx, outcome=outcome, deps=deps)


async def acknowledge_success_if_enabled(
    *,
    client: AsyncZammadClient,
    ctx: _TicketJobContext,
    outcome: _ArchiveOutcome,
) -> None:
    if not ctx.settings.workflow.acknowledge_on_success:
        return
    storage_result = outcome.storage_result
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
            timestamp_utc=format_timestamp_utc(outcome.now),
        ),
    )
