from __future__ import annotations

import asyncio

from zammad_pdf_archiver.app.jobs.shutdown import is_shutting_down
from zammad_pdf_archiver.config.settings import Settings
from zammad_pdf_archiver.domain.idempotency import InMemoryTTLSet

_DELIVERY_ID_SETS: dict[int, InMemoryTTLSet] = {}
_STORE_GUARD = asyncio.Lock()
_IN_FLIGHT_TICKETS: set[int] = set()
_IN_FLIGHT_TICKETS_GUARD = asyncio.Lock()


def _get_delivery_id_store(settings: Settings) -> InMemoryTTLSet | None:
    ttl = int(settings.workflow.delivery_id_ttl_seconds)
    if ttl <= 0 or is_shutting_down():
        return None
    store = _DELIVERY_ID_SETS.get(ttl)
    if store is None:
        store = InMemoryTTLSet(ttl_seconds=float(ttl))
        _DELIVERY_ID_SETS[ttl] = store
    return store


async def try_claim_delivery_id(settings: Settings, delivery_id: str) -> bool:
    async with _STORE_GUARD:
        store = _get_delivery_id_store(settings)
        if store is None:
            return True
        return await store.try_claim(delivery_id)


async def try_acquire_ticket(_settings: Settings, ticket_id: int) -> bool:
    async with _IN_FLIGHT_TICKETS_GUARD:
        if ticket_id in _IN_FLIGHT_TICKETS:
            return False
        _IN_FLIGHT_TICKETS.add(ticket_id)
        return True


async def release_ticket(ticket_id: int) -> None:
    async with _IN_FLIGHT_TICKETS_GUARD:
        _IN_FLIGHT_TICKETS.discard(ticket_id)


async def aclose_stores() -> None:
    async with _STORE_GUARD:
        _DELIVERY_ID_SETS.clear()
    async with _IN_FLIGHT_TICKETS_GUARD:
        _IN_FLIGHT_TICKETS.clear()


def reset_for_tests() -> None:
    _DELIVERY_ID_SETS.clear()
    _IN_FLIGHT_TICKETS.clear()
