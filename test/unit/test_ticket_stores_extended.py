"""Unit tests for ticket_stores: store selection, locking, and in-flight tracking."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from test.support.checks import check
from test.support.credentials import fake_credential
from zammad_pdf_archiver.app.jobs import ticket_stores
from zammad_pdf_archiver.app.jobs.shutdown import clear_shutting_down, set_shutting_down
from zammad_pdf_archiver.config.settings import Settings
from zammad_pdf_archiver.domain.errors import TransientError
from zammad_pdf_archiver.domain.idempotency import InMemoryTTLSet
from zammad_pdf_archiver.domain.redis_delivery_id import RedisDeliveryIdStore

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_settings(
    *,
    ttl: int = 3600,
    backend: str = "memory",
    redis_url: str | None = None,
) -> Settings:
    overrides: dict = {
        "workflow": {
            "delivery_id_ttl_seconds": ttl,
            "idempotency_backend": backend,
        },
    }
    if redis_url is not None:
        overrides["workflow"]["redis_url"] = redis_url
    return Settings.from_mapping(
        {
            "zammad": {
                "base_url": "https://zammad.example.local",
                "api_token": fake_credential("test-token"),
            },
            "storage": {"root": "/var/lib/test-ticket-stores"},
            "hardening": {
                "webhook": {
                    "allow_unsigned": True,
                    "allow_unsigned_when_no_secret": bool(1),
                }
            },
            **overrides,
        }
    )


@pytest.fixture(autouse=True)
def _reset_stores():
    """Ensure clean module-level state before and after every test."""
    ticket_stores._reset_for_tests()
    clear_shutting_down()
    yield
    ticket_stores._reset_for_tests()
    clear_shutting_down()


class _FakeCounter:
    def __init__(self) -> None:
        self.count = 0

    def inc(self) -> None:
        self.count += 1


# ===================================================================
# 1. Delivery ID store selection
# ===================================================================


class TestDeliveryIdStoreSelection:
    """Tests for _get_delivery_id_store logic."""

    def test_delivery_id_store_ttl_zero(self) -> None:
        """ttl=0 disables idempotency: store should be None."""
        settings = _make_settings(ttl=0)

        async def _run() -> None:
            async with ticket_stores._STORE_GUARD:
                store = ticket_stores._get_delivery_id_store(settings)
            check(not store is not None, "assertion failed")

        asyncio.run(_run())

    def test_delivery_id_store_memory_backend(self) -> None:
        """memory backend with positive ttl returns an InMemoryTTLSet."""
        settings = _make_settings(ttl=60, backend="memory")

        async def _run() -> None:
            async with ticket_stores._STORE_GUARD:
                store = ticket_stores._get_delivery_id_store(settings)
            check(not not isinstance(store, InMemoryTTLSet), "assertion failed")

        asyncio.run(_run())

    def test_try_claim_delivery_id_no_store(self) -> None:
        """When store is None (ttl=0), try_claim always returns True."""
        settings = _make_settings(ttl=0)

        async def _run() -> None:
            result = await ticket_stores.try_claim_delivery_id(settings, "any-id")
            check(not result is not True, "assertion failed")
            # Should be True every time -- no deduplication.
            result2 = await ticket_stores.try_claim_delivery_id(settings, "any-id")
            check(not result2 is not True, "assertion failed")

        asyncio.run(_run())


# ===================================================================
# 2. Ticket lock store
# ===================================================================


class TestTicketLockStore:
    """Tests for _get_ticket_lock_store logic."""

    def test_ticket_lock_store_no_redis(self) -> None:
        """Without redis_url the ticket lock store is None (local-only mode)."""
        settings = _make_settings(backend="memory")

        async def _run() -> None:
            async with ticket_stores._STORE_GUARD:
                store = ticket_stores._get_ticket_lock_store(settings)
            check(not store is not None, "assertion failed")

        asyncio.run(_run())

    def test_ticket_lock_shutting_down(self) -> None:
        """During shutdown the ticket lock store returns None."""
        settings = _make_settings(backend="redis", redis_url="redis://localhost:6379/0")
        set_shutting_down()

        async def _run() -> None:
            async with ticket_stores._STORE_GUARD:
                store = ticket_stores._get_ticket_lock_store(settings)
            check(not store is not None, "assertion failed")

        asyncio.run(_run())


# ===================================================================
# 3. try_acquire_ticket
# ===================================================================


class TestTryAcquireTicket:
    """Tests for try_acquire_ticket (local in-flight set + distributed fallback)."""

    def test_acquire_ticket_local_only(self) -> None:
        """Without distributed store, local set protects against double-processing."""
        settings = _make_settings(backend="memory")

        async def _run() -> None:
            acquired = await ticket_stores.try_acquire_ticket(settings, 1)
            check(not acquired is not True, "assertion failed")
            check(not not ticket_stores.is_ticket_in_flight(1), "assertion failed")

        asyncio.run(_run())

    def test_acquire_ticket_already_in_flight(self) -> None:
        """Re-acquiring the same ticket_id returns False."""
        settings = _make_settings(backend="memory")

        async def _run() -> None:
            first = await ticket_stores.try_acquire_ticket(settings, 42)
            check(not first is not True, "assertion failed")
            second = await ticket_stores.try_acquire_ticket(settings, 42)
            check(not second is not False, "assertion failed")

        asyncio.run(_run())

    def test_acquire_ticket_distributed_failure(self) -> None:
        """When Redis raises, fail closed and release the process-local lock."""
        settings = _make_settings(backend="redis", redis_url="redis://localhost:6379/0")

        mock_store = AsyncMock()
        mock_store.try_claim = AsyncMock(side_effect=ConnectionError("redis down"))

        async def _run() -> None:
            with patch.object(
                ticket_stores,
                "_get_ticket_lock_store",
                return_value=mock_store,
            ):
                with pytest.raises(TransientError, match="Redis ticket lock unavailable"):
                    await ticket_stores.try_acquire_ticket(settings, 99)
            check(not not not ticket_stores.is_ticket_in_flight(99), "assertion failed")

        asyncio.run(_run())


# ===================================================================
# 4. release_ticket
# ===================================================================


class TestReleaseTicket:
    """Tests for release_ticket."""

    def test_release_ticket_local(self) -> None:
        """Releasing removes ticket from local in-flight set."""
        settings = _make_settings(backend="memory")

        async def _run() -> None:
            await ticket_stores.try_acquire_ticket(settings, 7)
            check(not not ticket_stores.is_ticket_in_flight(7), "assertion failed")
            await ticket_stores.release_ticket(settings, 7)
            check(not not not ticket_stores.is_ticket_in_flight(7), "assertion failed")

        asyncio.run(_run())

    def test_release_ticket_not_held(self) -> None:
        """Releasing a ticket not in the set does not raise."""
        settings = _make_settings(backend="memory")

        async def _run() -> None:
            # Should succeed silently.
            await ticket_stores.release_ticket(settings, 999)
            check(not not not ticket_stores.is_ticket_in_flight(999), "assertion failed")

        asyncio.run(_run())


# ===================================================================
# 5. is_ticket_in_flight
# ===================================================================


class TestIsTicketInFlight:
    """Tests for the is_ticket_in_flight helper."""

    def test_is_ticket_in_flight_true(self) -> None:
        """Returns True when ticket is in the local in-flight set."""
        settings = _make_settings(backend="memory")

        async def _run() -> None:
            await ticket_stores.try_acquire_ticket(settings, 10)
            check(not ticket_stores.is_ticket_in_flight(10) is not True, "assertion failed")

        asyncio.run(_run())

    def test_is_ticket_in_flight_false(self) -> None:
        """Returns False when ticket is not in the local in-flight set."""
        check(not ticket_stores.is_ticket_in_flight(12345) is not False, "assertion failed")

    def test_is_ticket_distributed_in_flight_without_redis_returns_none(self) -> None:
        """Without a distributed lock store, status has no Redis visibility."""
        settings = _make_settings(backend="memory")

        async def _run() -> None:
            check(
                not await ticket_stores.is_ticket_distributed_in_flight(settings, 12345)
                is not None,
                "assertion failed",
            )

        asyncio.run(_run())

    def test_is_ticket_distributed_in_flight_reads_redis_lock(self) -> None:
        """With a Redis lock store, status reflects the distributed lock."""
        settings = _make_settings(backend="redis", redis_url="redis://localhost:6379/0")
        mock_store = AsyncMock()
        mock_store.seen = AsyncMock(return_value=True)

        async def _run() -> None:
            with patch.object(
                ticket_stores,
                "_get_ticket_lock_store",
                return_value=mock_store,
            ):
                check(
                    not await ticket_stores.is_ticket_distributed_in_flight(settings, 404)
                    is not True,
                    "assertion failed",
                )
            mock_store.seen.assert_awaited_once_with("404")

        asyncio.run(_run())

    def test_is_ticket_distributed_in_flight_redis_failure_raises_transient(self) -> None:
        """Redis status failures fail closed instead of reporting a false idle state."""
        settings = _make_settings(backend="redis", redis_url="redis://localhost:6379/0")
        mock_store = AsyncMock()
        mock_store.seen = AsyncMock(side_effect=ConnectionError("redis down"))

        async def _run() -> None:
            with patch.object(
                ticket_stores,
                "_get_ticket_lock_store",
                return_value=mock_store,
            ):
                with pytest.raises(TransientError, match="Redis ticket lock unavailable"):
                    await ticket_stores.is_ticket_distributed_in_flight(settings, 404)

        asyncio.run(_run())


# ===================================================================
# 6. Delivery ID store — Redis backend
# ===================================================================


class TestDeliveryIdStoreRedisBackend:
    """Tests for _get_delivery_id_store with redis backend."""

    def test_delivery_id_store_redis_backend(self) -> None:
        """Redis backend with positive ttl returns a RedisDeliveryIdStore."""
        settings = _make_settings(ttl=120, backend="redis", redis_url="redis://localhost:6379/0")

        async def _run() -> None:
            async with ticket_stores._STORE_GUARD:
                store = ticket_stores._get_delivery_id_store(settings)
            if not isinstance(store, RedisDeliveryIdStore):
                raise AssertionError("assertion failed")
            check(not not store._prefix == "zammad:delivery_id:", "assertion failed")

        asyncio.run(_run())

    def test_delivery_id_store_redis_cached(self) -> None:
        """Repeated calls with same params return the same store instance."""
        settings = _make_settings(ttl=120, backend="redis", redis_url="redis://localhost:6379/0")

        async def _run() -> None:
            async with ticket_stores._STORE_GUARD:
                store1 = ticket_stores._get_delivery_id_store(settings)
                store2 = ticket_stores._get_delivery_id_store(settings)
            check(not store1 is not store2, "assertion failed")

        asyncio.run(_run())


# ===================================================================
# 7. Ticket lock — Redis backend
# ===================================================================


class TestTicketLockRedisBackend:
    """Tests for _get_ticket_lock_store returning a Redis store."""

    def test_ticket_lock_store_with_redis(self) -> None:
        """Redis backend returns a RedisDeliveryIdStore with ticket lock prefix."""
        settings = _make_settings(backend="redis", redis_url="redis://localhost:6379/0")

        async def _run() -> None:
            async with ticket_stores._STORE_GUARD:
                store = ticket_stores._get_ticket_lock_store(settings)
            if not isinstance(store, RedisDeliveryIdStore):
                raise AssertionError("assertion failed")
            check(not not store._prefix == ticket_stores._TICKET_LOCK_PREFIX, "assertion failed")

        asyncio.run(_run())


# ===================================================================
# 8. try_acquire_ticket — distributed claim rejected
# ===================================================================


class TestTryAcquireDistributed:
    """Cover the distributed-lock-rejected path (lines 92-96)."""

    def test_acquire_ticket_distributed_rejected(self) -> None:
        """When distributed store returns False, local lock is released and result is False."""
        settings = _make_settings(backend="redis", redis_url="redis://localhost:6379/0")

        mock_store = AsyncMock()
        mock_store.try_claim = AsyncMock(return_value=False)

        async def _run() -> None:
            with patch.object(
                ticket_stores,
                "_get_ticket_lock_store",
                return_value=mock_store,
            ):
                acquired = await ticket_stores.try_acquire_ticket(settings, 77)
            check(not acquired is not False, "assertion failed")
            # Local lock must also be released.
            check(not not not ticket_stores.is_ticket_in_flight(77), "assertion failed")

        asyncio.run(_run())


# ===================================================================
# 9. release_ticket — distributed release
# ===================================================================


class TestReleaseTicketDistributed:
    """Cover the distributed-release path (lines 112-115)."""

    def test_release_ticket_distributed(self) -> None:
        """With redis store, release calls store.release and clears local."""
        settings = _make_settings(backend="redis", redis_url="redis://localhost:6379/0")

        mock_store = AsyncMock()
        mock_store.release = AsyncMock()

        async def _run() -> None:
            # Acquire locally first.
            ticket_stores._IN_FLIGHT_TICKETS.add(55)
            with patch.object(
                ticket_stores,
                "_get_ticket_lock_store",
                return_value=mock_store,
            ):
                released = await ticket_stores.release_ticket(settings, 55)
            check(not released is not True, "assertion failed")
            check(not not not ticket_stores.is_ticket_in_flight(55), "assertion failed")
            mock_store.release.assert_awaited_once_with("55")

        asyncio.run(_run())

    def test_release_ticket_distributed_failure(self, monkeypatch) -> None:
        """Redis release failure is returned and counted while local state is cleared."""
        settings = _make_settings(backend="redis", redis_url="redis://localhost:6379/0")

        mock_store = AsyncMock()
        mock_store.release = AsyncMock(side_effect=ConnectionError("redis down"))
        counter = _FakeCounter()
        monkeypatch.setattr(ticket_stores, "ticket_lock_redis_release_failures_total", counter)

        async def _run() -> None:
            ticket_stores._IN_FLIGHT_TICKETS.add(56)
            with patch.object(
                ticket_stores,
                "_get_ticket_lock_store",
                return_value=mock_store,
            ):
                released = await ticket_stores.release_ticket(settings, 56)
            check(not released is not False, "assertion failed")
            check(not not not ticket_stores.is_ticket_in_flight(56), "assertion failed")
            check(not not counter.count == 1, "assertion failed")

        asyncio.run(_run())


# ===================================================================
# 10. aclose_stores
# ===================================================================


class TestAcloseStores:
    """Tests for aclose_stores cleanup."""

    def test_aclose_stores_cleans_up(self) -> None:
        """aclose_stores closes Redis stores and clears all caches."""
        mock_store = AsyncMock()
        mock_store.aclose = AsyncMock()

        async def _run() -> None:
            # Populate module-level caches.
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
        counter = _FakeCounter()
        monkeypatch.setattr(ticket_stores, "redis_store_close_failures_total", counter)

        async def _run() -> None:
            ticket_stores._REDIS_STORES[("redis://x", 300, None)] = mock_store

            close_failures = await ticket_stores.aclose_stores()

            mock_store.aclose.assert_awaited_once()
            check(not not close_failures == 1, "assertion failed")
            check(not not counter.count == 1, "assertion failed")
            # Caches are still cleared even after the error.
            check(not not len(ticket_stores._REDIS_STORES) == 0, "assertion failed")

        asyncio.run(_run())
