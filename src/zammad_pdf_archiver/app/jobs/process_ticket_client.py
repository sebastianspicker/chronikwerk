from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from zammad_pdf_archiver.adapters.zammad.client import ZammadClientTransportOptions
from zammad_pdf_archiver.app.constants import FORCE_REPROCESS_KEY
from zammad_pdf_archiver.app.jobs._process_ticket_models import (
    SKIPPED_PROCESS_STATUSES,
    ProcessTicketResult,
    _TicketJobContext,
)
from zammad_pdf_archiver.domain.state_machine import TRIGGER_TAG

RunTicketPipeline = Callable[..., Awaitable[ProcessTicketResult]]
HandlePipelineException = Callable[..., Awaitable[ProcessTicketResult]]


async def process_ticket_with_client(
    ctx: _TicketJobContext,
    *,
    payload: dict[str, Any],
    client_cls: Any,
    run_ticket_pipeline: RunTicketPipeline,
    handle_pipeline_exception: HandlePipelineException,
    observe_total_seconds: Callable[[float], None],
    clock: Callable[[], float],
) -> ProcessTicketResult:
    """Open a Zammad client session and drive the full ticket archival pipeline."""
    settings = ctx.settings
    trigger_tag = str(settings.workflow.trigger_tag).strip() or TRIGGER_TAG
    require_trigger_tag = bool(settings.workflow.require_tag)
    force_reprocess = payload.get(FORCE_REPROCESS_KEY) is True

    async with client_cls(
        base_url=str(settings.zammad.base_url),
        api_token=settings.zammad.api_token.get_secret_value(),
        transport=ZammadClientTransportOptions(
            timeout_seconds=settings.zammad.timeout_seconds,
            verify_tls=settings.zammad.verify_tls,
            trust_env=settings.hardening.transport.trust_env,
        ),
    ) as client:
        result: ProcessTicketResult | None = None
        total_start = clock()
        try:
            result = await run_ticket_pipeline(
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
            result = await handle_pipeline_exception(
                client=client,
                ctx=ctx,
                trigger_tag=trigger_tag,
                exc=exc,
            )
            return result
        finally:
            if result is None or result.status not in SKIPPED_PROCESS_STATUSES:
                observe_total_seconds(clock() - total_start)
