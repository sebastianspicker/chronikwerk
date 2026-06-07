"""Ticket-store in-flight visibility tests."""

from __future__ import annotations

import asyncio as _asyncio
from unittest.mock import AsyncMock as _AsyncMock
from unittest.mock import patch as _patch

import pytest

from test.support.checks import check
from test.support.ticket_stores_helpers import make_ticket_store_settings as _make_settings
from test.support.ticket_stores_helpers import reset_stores as _reset_stores  # noqa: F401
from zammad_pdf_archiver.app.jobs import ticket_stores
from zammad_pdf_archiver.domain.errors import TransientError


class TestIsTicketInFlight:
    """Tests for the is_ticket_in_flight helper."""

    def test_is_ticket_in_flight_true(self) -> None:
        """Returns True when ticket is in the local in-flight set."""
        settings = _make_settings(backend="memory")

        async def _run() -> None:
            await ticket_stores.try_acquire_ticket(settings, 10)
            check(not ticket_stores.is_ticket_in_flight(10) is not True, "assertion failed")

        _asyncio.run(_run())

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

        _asyncio.run(_run())

    def test_is_ticket_distributed_in_flight_reads_redis_lock(self) -> None:
        """With a Redis lock store, status reflects the distributed lock."""
        settings = _make_settings(backend="redis", redis_url="redis://localhost:6379/0")
        mock_store = _AsyncMock()
        mock_store.seen = _AsyncMock(return_value=True)

        async def _run() -> None:
            with _patch.object(
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

        _asyncio.run(_run())

    def test_is_ticket_distributed_in_flight_redis_failure_raises_transient(self) -> None:
        """Redis status failures fail closed instead of reporting a false idle state."""
        settings = _make_settings(backend="redis", redis_url="redis://localhost:6379/0")
        mock_store = _AsyncMock()
        mock_store.seen = _AsyncMock(side_effect=ConnectionError("redis down"))

        async def _run() -> None:
            with _patch.object(
                ticket_stores,
                "_get_ticket_lock_store",
                return_value=mock_store,
            ):
                with pytest.raises(TransientError, match="Redis ticket lock unavailable"):
                    await ticket_stores.is_ticket_distributed_in_flight(settings, 404)

        _asyncio.run(_run())
