"""Unit tests for ticket_stores: store selection, locking, and in-flight tracking."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from test.support.checks import check
from test.support.ticket_stores_helpers import make_ticket_store_settings as _make_settings
from test.support.ticket_stores_helpers import reset_stores as _reset_stores  # noqa: F401
from zammad_pdf_archiver.app.jobs import ticket_stores
from zammad_pdf_archiver.app.jobs.shutdown import set_shutting_down
from zammad_pdf_archiver.domain.errors import TransientError
from zammad_pdf_archiver.domain.idempotency import InMemoryTTLSet

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
