"""Project module."""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any

TRIGGER_TAG = "pdf:sign"
PROCESSING_TAG = "pdf:processing"
DONE_TAG = "pdf:signed"
ERROR_TAG = "pdf:error"


def should_process(
    tags: Iterable[str] | None,
    *,
    trigger_tag: str = TRIGGER_TAG,
    require_trigger_tag: bool = True,
) -> bool:
    """Implement the should process operation."""
    tag_set = set(tags or [])
    if DONE_TAG in tag_set:
        return False
    if require_trigger_tag:
        return trigger_tag in tag_set
    return True


async def apply_processing(
    client: Any,
    ticket_id: int,
    *,
    trigger_tag: str = TRIGGER_TAG,
    force_reprocess: bool = False,
) -> None:
    """Implement the apply processing operation."""
    if force_reprocess:
        await client.remove_tag(ticket_id, DONE_TAG)
    await client.remove_tag(ticket_id, ERROR_TAG)
    await client.remove_tag(ticket_id, trigger_tag)
    await client.add_tag(ticket_id, PROCESSING_TAG)


async def apply_done(client: Any, ticket_id: int, *, trigger_tag: str = TRIGGER_TAG) -> None:
    """Implement the apply done operation."""
    await client.remove_tag(ticket_id, PROCESSING_TAG)
    await client.remove_tag(ticket_id, ERROR_TAG)
    await client.remove_tag(ticket_id, trigger_tag)
    await client.add_tag(ticket_id, DONE_TAG)


async def apply_error(
    client: Any,
    ticket_id: int,
    *,
    keep_trigger: bool = True,
    trigger_tag: str = TRIGGER_TAG,
) -> None:
    """Implement the apply error operation."""
    await client.remove_tag(ticket_id, PROCESSING_TAG)
    await client.remove_tag(ticket_id, DONE_TAG)
    if keep_trigger:
        await client.add_tag(ticket_id, trigger_tag)
    else:
        await client.remove_tag(ticket_id, trigger_tag)
    await client.add_tag(ticket_id, ERROR_TAG)
