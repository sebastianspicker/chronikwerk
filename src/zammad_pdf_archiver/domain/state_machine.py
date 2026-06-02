from __future__ import annotations

from collections.abc import Iterable
from typing import Any

TRIGGER_TAG = "pdf:sign"
PROCESSING_TAG = "pdf:processing"
DONE_TAG = "pdf:signed"
ERROR_TAG = "pdf:error"

# Race condition warning for multi-instance deployments:
#
# The tag-based state machine (should_process -> apply_processing -> apply_done/apply_error)
# is NOT atomic.  Between the should_process() check and the apply_processing() tag write,
# another instance may read the same tags and also decide to process the ticket.  This can
# lead to duplicate processing.
#
# In single-instance deployments the in-process ticket lock is sufficient.  In multi-instance
# deployments you MUST use a distributed lock / idempotency backend (Redis) to prevent
# duplicate work.  Specifically, configure:
#   - idempotency_backend = "redis"
#   - execution_backend  = "redis_queue"
#
# Without Redis-backed coordination, two instances can race on the tag check and both enter
# the processing pipeline for the same ticket.


def should_process(
    tags: Iterable[str] | None,
    *,
    trigger_tag: str,
    require_trigger_tag: bool = True,
) -> bool:
    """Return True if the ticket's tags indicate it should be processed."""
    tag_set = set(tags or [])
    if DONE_TAG in tag_set:
        return False
    # A processing tag means another worker already moved the ticket into the
    # in-flight state; even if the trigger tag is still present, do not start a
    # second archive attempt.
    if PROCESSING_TAG in tag_set:
        return False
    if require_trigger_tag:
        return trigger_tag in tag_set
    return True


async def apply_processing(
    client: Any,
    ticket_id: int,
    *,
    trigger_tag: str,
    force_reprocess: bool = False,
) -> None:
    """Idempotent tag transition: any state -> processing."""
    if force_reprocess:
        await client.remove_tag(ticket_id, DONE_TAG)
    await client.remove_tag(ticket_id, ERROR_TAG)
    await client.remove_tag(ticket_id, trigger_tag)
    await client.add_tag(ticket_id, PROCESSING_TAG)


async def apply_done(client: Any, ticket_id: int, *, trigger_tag: str) -> None:
    """Idempotent tag transition: any state -> done."""
    await client.remove_tag(ticket_id, PROCESSING_TAG)
    await client.remove_tag(ticket_id, ERROR_TAG)
    await client.remove_tag(ticket_id, trigger_tag)
    await client.add_tag(ticket_id, DONE_TAG)


async def apply_error(
    client: Any,
    ticket_id: int,
    *,
    keep_trigger: bool = True,
    trigger_tag: str,
) -> None:
    """Idempotent tag transition: any state -> error."""
    await client.remove_tag(ticket_id, PROCESSING_TAG)
    await client.remove_tag(ticket_id, DONE_TAG)
    if keep_trigger:
        await client.add_tag(ticket_id, trigger_tag)
    else:
        await client.remove_tag(ticket_id, trigger_tag)
    await client.add_tag(ticket_id, ERROR_TAG)
