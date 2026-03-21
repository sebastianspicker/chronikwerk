"""Unit tests for ticket_stores: store selection, locking, and in-flight tracking."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from zammad_pdf_archiver.app.jobs import ticket_stores
from zammad_pdf_archiver.app.jobs.shutdown import clear_shutting_down, set_shutting_down
from zammad_pdf_archiver.config.settings import Settings
from zammad_pdf_archiver.domain.idempotency import InMemoryTTLSet

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
            "zammad": {"base_url": "https://zammad.example.local", "api_token": "test-token"},
            "storage": {"root": "/tmp/test-ticket-stores"},
            "hardening": {
                "webhook": {
                    "allow_unsigned": True,
                    "allow_unsigned_when_no_secret": True,
                }
            },
            **overrides,
        }
    )


@pytest.fixture(autouse=True)
def _reset_stores():
    """Ensure clean module-level state before and after every test."""
    ticket_stores.reset_for_tests()
    clear_shutting_down()
    yield
    ticket_stores.reset_for_tests()
    clear_shutting_down()


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
            assert store is None

        asyncio.run(_run())

    def test_delivery_id_store_memory_backend(self) -> None:
        """memory backend with positive ttl returns an InMemoryTTLSet."""
        settings = _make_settings(ttl=60, backend="memory")

        async def _run() -> None:
            async with ticket_stores._STORE_GUARD:
                store = ticket_stores._get_delivery_id_store(settings)
            assert isinstance(store, InMemoryTTLSet)

        asyncio.run(_run())

    def test_try_claim_delivery_id_no_store(self) -> None:
        """When store is None (ttl=0), try_claim always returns True."""
        settings = _make_settings(ttl=0)

        async def _run() -> None:
            result = await ticket_stores.try_claim_delivery_id(settings, "any-id")
            assert result is True
            # Should be True every time -- no deduplication.
            result2 = await ticket_stores.try_claim_delivery_id(settings, "any-id")
            assert result2 is True

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
            assert store is None

        asyncio.run(_run())

    def test_ticket_lock_shutting_down(self) -> None:
        """During shutdown the ticket lock store returns None."""
        settings = _make_settings(backend="redis", redis_url="redis://localhost:6379/0")
        set_shutting_down()

        async def _run() -> None:
            async with ticket_stores._STORE_GUARD:
                store = ticket_stores._get_ticket_lock_store(settings)
            assert store is None

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
            assert acquired is True
            assert ticket_stores.is_ticket_in_flight(1)

        asyncio.run(_run())

    def test_acquire_ticket_already_in_flight(self) -> None:
        """Re-acquiring the same ticket_id returns False."""
        settings = _make_settings(backend="memory")

        async def _run() -> None:
            first = await ticket_stores.try_acquire_ticket(settings, 42)
            assert first is True
            second = await ticket_stores.try_acquire_ticket(settings, 42)
            assert second is False

        asyncio.run(_run())

    def test_acquire_ticket_distributed_failure(self) -> None:
        """When Redis raises, fallback to local lock succeeds (logs warning)."""
        settings = _make_settings(backend="redis", redis_url="redis://localhost:6379/0")

        mock_store = AsyncMock()
        mock_store.try_claim = AsyncMock(side_effect=ConnectionError("redis down"))

        async def _run() -> None:
            with patch.object(
                ticket_stores,
                "_get_ticket_lock_store",
                return_value=mock_store,
            ):
                acquired = await ticket_stores.try_acquire_ticket(settings, 99)
            assert acquired is True
            assert ticket_stores.is_ticket_in_flight(99)

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
            assert ticket_stores.is_ticket_in_flight(7)
            await ticket_stores.release_ticket(settings, 7)
            assert not ticket_stores.is_ticket_in_flight(7)

        asyncio.run(_run())

    def test_release_ticket_not_held(self) -> None:
        """Releasing a ticket not in the set does not raise."""
        settings = _make_settings(backend="memory")

        async def _run() -> None:
            # Should succeed silently.
            await ticket_stores.release_ticket(settings, 999)
            assert not ticket_stores.is_ticket_in_flight(999)

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
            assert ticket_stores.is_ticket_in_flight(10) is True

        asyncio.run(_run())

    def test_is_ticket_in_flight_false(self) -> None:
        """Returns False when ticket is not in the local in-flight set."""
        assert ticket_stores.is_ticket_in_flight(12345) is False
