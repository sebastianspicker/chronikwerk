import asyncio

import structlog

from zammad_pdf_archiver.app.jobs.ticket_store_selection import (
    delivery_id_store,
    redis_store,
    ticket_lock_store,
)
from zammad_pdf_archiver.config.settings import Settings
from zammad_pdf_archiver.domain.errors import TransientError
from zammad_pdf_archiver.domain.idempotency import DeliveryIdStore, InMemoryTTLSet
from zammad_pdf_archiver.domain.redis_delivery_id import RedisDeliveryIdStore
from zammad_pdf_archiver.observability.metrics import (
    redis_store_close_failures_total,
    ticket_lock_redis_failures_total,
    ticket_lock_redis_release_failures_total,
    tickets_in_flight,
)

log = structlog.get_logger(__name__)

_DELIVERY_ID_SETS: dict[int, InMemoryTTLSet] = {}
_REDIS_STORES: dict[tuple[str, int, str | None], RedisDeliveryIdStore] = {}
_STORE_GUARD = asyncio.Lock()

_IN_FLIGHT_TICKETS: set[int] = set()
_IN_FLIGHT_TICKETS_GUARD = asyncio.Lock()
# Lock order for related ticket state is always _STORE_GUARD, then
# _IN_FLIGHT_TICKETS_GUARD. Do not acquire these locks in the reverse order.
_TICKET_LOCK_PREFIX = "zammad:ticket_lock:"
_TICKET_LOCK_TTL = 1800  # 30 minutes — long enough for slow PDF rendering and signing


def _get_redis_store(
    redis_url: str,
    ttl_seconds: int,
    prefix: str | None = None,
) -> RedisDeliveryIdStore:
    """Helper to deduplicate Redis store initialization."""
    return redis_store(_REDIS_STORES, redis_url, ttl_seconds, prefix)


def _get_delivery_id_store(settings: Settings) -> DeliveryIdStore | None:
    """Delivery-ID store or None if idempotency off (ttl<=0). Caller must hold _STORE_GUARD."""
    return delivery_id_store(
        settings,
        memory_sets=_DELIVERY_ID_SETS,
        redis_stores=_REDIS_STORES,
    )


async def try_claim_delivery_id(settings: Settings, delivery_id: str) -> bool:
    """
    Atomically check and register delivery_id for idempotency.
    Returns True if this delivery was newly claimed (caller should proceed),
    False if already seen (caller should skip).
    """
    async with _STORE_GUARD:
        store = _get_delivery_id_store(settings)
        if store is None:
            return True
        return await store.try_claim(delivery_id)


def _get_ticket_lock_store(settings: Settings) -> RedisDeliveryIdStore | None:
    """Distributed ticket lock store or None if Redis off. Caller must hold _STORE_GUARD."""
    return ticket_lock_store(
        settings,
        redis_stores=_REDIS_STORES,
        ttl_seconds=_TICKET_LOCK_TTL,
        prefix=_TICKET_LOCK_PREFIX,
    )


async def try_acquire_ticket(settings: Settings, ticket_id: int) -> bool:
    async with _STORE_GUARD:
        async with _IN_FLIGHT_TICKETS_GUARD:
            if ticket_id in _IN_FLIGHT_TICKETS:
                return False
            _IN_FLIGHT_TICKETS.add(ticket_id)
            tickets_in_flight.set(len(_IN_FLIGHT_TICKETS))

        store = _get_ticket_lock_store(settings)
        try:
            if store is not None:
                claimed = await store.try_claim(str(ticket_id))
                if not claimed:
                    async with _IN_FLIGHT_TICKETS_GUARD:
                        _IN_FLIGHT_TICKETS.discard(ticket_id)
                        tickets_in_flight.set(len(_IN_FLIGHT_TICKETS))
                    return False
        except Exception:
            async with _IN_FLIGHT_TICKETS_GUARD:
                _IN_FLIGHT_TICKETS.discard(ticket_id)
                tickets_in_flight.set(len(_IN_FLIGHT_TICKETS))
            ticket_lock_redis_failures_total.inc()
            log.error(
                "process_ticket.redis_lock_failed",
                ticket_id=ticket_id,
            )
            raise TransientError("Redis ticket lock unavailable") from None

    return True


async def release_ticket(settings: Settings, ticket_id: int) -> bool:
    """Release a ticket lock; return False when Redis release did not complete."""
    remote_released = True
    async with _STORE_GUARD:
        store = _get_ticket_lock_store(settings)
        if store is not None:
            try:
                await store.release(str(ticket_id))
            except Exception:
                remote_released = False
                ticket_lock_redis_release_failures_total.inc()
                log.warning("process_ticket.redis_unlock_failed", ticket_id=ticket_id)

        async with _IN_FLIGHT_TICKETS_GUARD:
            _IN_FLIGHT_TICKETS.discard(ticket_id)
            tickets_in_flight.set(len(_IN_FLIGHT_TICKETS))
    return remote_released


async def aclose_stores() -> int:
    """Close all persistent Redis stores and clear local caches."""
    close_failures = 0
    async with _STORE_GUARD:
        for store in _REDIS_STORES.values():
            try:
                await store.aclose()
            except Exception:
                close_failures += 1
                redis_store_close_failures_total.inc()
                log.warning("process_ticket.redis_store_aclose_failed")
        _REDIS_STORES.clear()
        _DELIVERY_ID_SETS.clear()

        async with _IN_FLIGHT_TICKETS_GUARD:
            _IN_FLIGHT_TICKETS.clear()
            tickets_in_flight.set(0)
    return close_failures


def _reset_for_tests() -> None:
    """
    Clear in-memory caches used by idempotency and local in-flight guards.

    Intended for tests that need deterministic start state.
    """
    if _STORE_GUARD.locked() or _IN_FLIGHT_TICKETS_GUARD.locked():
        raise RuntimeError("ticket store reset requested while store locks are held")
    _DELIVERY_ID_SETS.clear()
    _REDIS_STORES.clear()
    _IN_FLIGHT_TICKETS.clear()
    tickets_in_flight.set(0)


def is_ticket_in_flight(ticket_id: int) -> bool:
    """Best-effort process-local visibility of in-flight ticket state."""
    return ticket_id in _IN_FLIGHT_TICKETS


async def is_ticket_distributed_in_flight(settings: Settings, ticket_id: int) -> bool | None:
    """Return Redis ticket-lock visibility, or None when no distributed lock is configured."""
    async with _STORE_GUARD:
        store = _get_ticket_lock_store(settings)
        if store is None:
            return None
        try:
            return await store.seen(str(ticket_id))
        except Exception:
            ticket_lock_redis_failures_total.inc()
            log.error("process_ticket.redis_lock_status_failed", ticket_id=ticket_id)
            raise TransientError("Redis ticket lock unavailable") from None
