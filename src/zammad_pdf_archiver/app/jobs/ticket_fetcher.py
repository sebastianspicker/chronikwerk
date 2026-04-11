"""Fetch a Zammad ticket and its tag list together as a typed bundle."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from zammad_pdf_archiver.adapters.zammad.models import TagList, Ticket

if TYPE_CHECKING:
    from zammad_pdf_archiver.adapters.zammad.client import AsyncZammadClient


@dataclass(frozen=True)
class TicketData:
    ticket: Ticket
    tags: TagList
    ticket_id: int


async def fetch_ticket_data(
    client: AsyncZammadClient,
    ticket_id: int,
) -> TicketData:
    ticket = await client.get_ticket(ticket_id)
    tags = await client.list_tags(ticket_id)

    return TicketData(
        ticket=ticket,
        tags=tags,
        ticket_id=ticket_id,
    )
