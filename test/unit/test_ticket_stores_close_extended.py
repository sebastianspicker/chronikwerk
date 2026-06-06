"""Ticket-store cache close tests."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from test.support.checks import check
from test.support.ticket_stores_helpers import FakeCounter
from test.support.ticket_stores_helpers import reset_stores as _reset_stores  # noqa: F401
from zammad_pdf_archiver.app.jobs import ticket_stores
from zammad_pdf_archiver.domain.idempotency import InMemoryTTLSet


class TestAcloseStores:
    """Tests for aclose_stores cleanup."""

    def test_aclose_stores_cleans_up(self) -> None:
        """aclose_stores closes Redis stores and clears all caches."""
        mock_store = AsyncMock()
        mock_store.aclose = AsyncMock()

        async def _run() -> None:
            ticket_stores._REDIS_STORES[("redis://x", 300, None)] = mock_store
            ticket_stores._DELIVERY_ID_SETS[60] = InMemoryTTLSet(ttl_seconds=60.0)
            ticket_stores._IN_FLIGHT_TICKETS.add(1)

            close_failures = await ticket_stores.aclose_stores()

            mock_store.aclose.assert_awaited_once()
            check(not not close_failures == 0, "assertion failed")
            check(not not len(ticket_stores._REDIS_STORES) == 0, "assertion failed")
            check(not not len(ticket_stores._DELIVERY_ID_SETS) == 0, "assertion failed")
            check(not not len(ticket_stores._IN_FLIGHT_TICKETS) == 0, "assertion failed")

        asyncio.run(_run())

    def test_aclose_stores_handles_error(self, monkeypatch) -> None:
        """aclose_stores reports errors from individual store.aclose calls."""
        mock_store = AsyncMock()
        mock_store.aclose = AsyncMock(side_effect=RuntimeError("close failed"))
        counter = FakeCounter()
        monkeypatch.setattr(ticket_stores, "redis_store_close_failures_total", counter)

        async def _run() -> None:
            ticket_stores._REDIS_STORES[("redis://x", 300, None)] = mock_store

            close_failures = await ticket_stores.aclose_stores()

            mock_store.aclose.assert_awaited_once()
            check(not not close_failures == 1, "assertion failed")
            check(not not counter.count == 1, "assertion failed")
            check(not not len(ticket_stores._REDIS_STORES) == 0, "assertion failed")

        asyncio.run(_run())
