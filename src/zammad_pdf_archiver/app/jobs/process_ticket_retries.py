from __future__ import annotations

import asyncio

from zammad_pdf_archiver.adapters.zammad.client import AsyncZammadClient
from zammad_pdf_archiver.domain.errors import PermanentError
from zammad_pdf_archiver.domain.state_machine import apply_done


async def apply_done_with_backoff(
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
