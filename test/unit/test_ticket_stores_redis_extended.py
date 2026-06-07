from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from test.support.checks import check
from test.support.ticket_stores_helpers import FakeCounter, make_ticket_store_settings
from test.support.ticket_stores_helpers import reset_stores as _reset_stores  # noqa: F401
from zammad_pdf_archiver.app.jobs import ticket_stores
from zammad_pdf_archiver.domain.redis_delivery_id import RedisDeliveryIdStore


class TestDeliveryIdStoreRedisBackend:
    """Tests for _get_delivery_id_store with redis backend."""

    def test_delivery_id_store_redis_backend(self) -> None:
        """Redis backend with positive ttl returns a RedisDeliveryIdStore."""
        settings = make_ticket_store_settings(
            ttl=120, backend="redis", redis_url="redis://localhost:6379/0"
        )

        async def _run() -> None:
            async with ticket_stores._STORE_GUARD:
                store = ticket_stores._get_delivery_id_store(settings)
            if not isinstance(store, RedisDeliveryIdStore):
                raise AssertionError("assertion failed")
            check(not not store._prefix == "zammad:delivery_id:", "assertion failed")

        asyncio.run(_run())

    def test_delivery_id_store_redis_cached(self) -> None:
        """Repeated calls with same params return the same store instance."""
        settings = make_ticket_store_settings(
            ttl=120, backend="redis", redis_url="redis://localhost:6379/0"
        )

        async def _run() -> None:
            async with ticket_stores._STORE_GUARD:
                store1 = ticket_stores._get_delivery_id_store(settings)
                store2 = ticket_stores._get_delivery_id_store(settings)
            check(not store1 is not store2, "assertion failed")

        asyncio.run(_run())


class TestTicketLockRedisBackend:
    """Tests for _get_ticket_lock_store returning a Redis store."""

    def test_ticket_lock_store_with_redis(self) -> None:
        """Redis backend returns a RedisDeliveryIdStore with ticket lock prefix."""
        settings = make_ticket_store_settings(backend="redis", redis_url="redis://localhost:6379/0")

        async def _run() -> None:
            async with ticket_stores._STORE_GUARD:
                store = ticket_stores._get_ticket_lock_store(settings)
            if not isinstance(store, RedisDeliveryIdStore):
                raise AssertionError("assertion failed")
            check(not not store._prefix == ticket_stores._TICKET_LOCK_PREFIX, "assertion failed")

        asyncio.run(_run())


def test_acquire_ticket_distributed_rejected() -> None:
    """When distributed store returns False, local lock is released and result is False."""
    settings = make_ticket_store_settings(backend="redis", redis_url="redis://localhost:6379/0")
    mock_store = AsyncMock()
    mock_store.try_claim = AsyncMock(return_value=False)

    async def _run() -> None:
        with patch.object(ticket_stores, "_get_ticket_lock_store", return_value=mock_store):
            acquired = await ticket_stores.try_acquire_ticket(settings, 77)
        check(not acquired is not False, "assertion failed")
        check(not not not ticket_stores.is_ticket_in_flight(77), "assertion failed")

    asyncio.run(_run())


class TestReleaseTicketDistributed:
    """Cover the distributed-release path."""

    def test_release_ticket_distributed(self) -> None:
        """With redis store, release calls store.release and clears local."""
        settings = make_ticket_store_settings(backend="redis", redis_url="redis://localhost:6379/0")
        mock_store = AsyncMock()
        mock_store.release = AsyncMock()

        async def _run() -> None:
            ticket_stores._IN_FLIGHT_TICKETS.add(55)
            with patch.object(ticket_stores, "_get_ticket_lock_store", return_value=mock_store):
                released = await ticket_stores.release_ticket(settings, 55)
            check(not released is not True, "assertion failed")
            check(not not not ticket_stores.is_ticket_in_flight(55), "assertion failed")
            mock_store.release.assert_awaited_once_with("55")

        asyncio.run(_run())

    def test_release_ticket_distributed_failure(self, monkeypatch) -> None:
        """Redis release failure is returned and counted while local state is cleared."""
        settings = make_ticket_store_settings(backend="redis", redis_url="redis://localhost:6379/0")
        mock_store = AsyncMock()
        mock_store.release = AsyncMock(side_effect=ConnectionError("redis down"))
        counter = FakeCounter()
        monkeypatch.setattr(ticket_stores, "ticket_lock_redis_release_failures_total", counter)

        async def _run() -> None:
            ticket_stores._IN_FLIGHT_TICKETS.add(56)
            with patch.object(ticket_stores, "_get_ticket_lock_store", return_value=mock_store):
                released = await ticket_stores.release_ticket(settings, 56)
            check(not released is not False, "assertion failed")
            check(not not not ticket_stores.is_ticket_in_flight(56), "assertion failed")
            check(not not counter.count == 1, "assertion failed")

        asyncio.run(_run())
