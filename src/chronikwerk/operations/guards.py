"""Own process-local per-ticket exclusion and delivery-ID deduplication."""

from __future__ import annotations

import time
from collections.abc import Callable

from chronikwerk.operations.idempotency import InMemoryTTLSet


class TicketGuards:
    """Volatile in-flight ticket set and delivery-ID claims for one application.

    Methods are synchronous and run on one event loop, so each check-and-set is
    atomic without an ``asyncio.Lock``.
    """

    def __init__(
        self,
        *,
        delivery_id_ttl_seconds: int,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        ttl = int(delivery_id_ttl_seconds)
        self._in_flight: set[int] = set()
        self._deliveries = InMemoryTTLSet(ttl_seconds=float(ttl), now=now) if ttl > 0 else None

    def try_acquire_ticket(self, ticket_id: int) -> bool:
        """Acquire per-ticket exclusion before concurrent archival begins."""
        if ticket_id in self._in_flight:
            return False
        self._in_flight.add(ticket_id)
        return True

    def release_ticket(self, ticket_id: int) -> None:
        """Release a ticket claim after its pipeline completes or aborts."""
        self._in_flight.discard(ticket_id)

    def try_claim_delivery(self, delivery_id: str) -> bool:
        """Claim a webhook delivery once; always succeed when deduplication is disabled."""
        if self._deliveries is None:
            return True
        return self._deliveries.try_claim(delivery_id)
