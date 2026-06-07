"""Redis pool close_all tests."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from test.support.checks import check
from test.support.redis_pool_helpers import FakeCounter


def test_close_all_closes_and_clears() -> None:
    """close_all() should call aclose() on each cached client, then clear the dict."""
    from zammad_pdf_archiver.adapters import redis_pool

    client1 = AsyncMock()
    client2 = AsyncMock()

    async def _run() -> None:
        redis_pool._CLIENTS.clear()
        redis_pool._CLIENTS["redis://a"] = client1
        redis_pool._CLIENTS["redis://b"] = client2
        try:
            close_failures = await redis_pool.close_all()
            client1.aclose.assert_awaited_once()
            client2.aclose.assert_awaited_once()
            check(not not close_failures == 0, "assertion failed")
            check(not not len(redis_pool._CLIENTS) == 0, "assertion failed")
        finally:
            redis_pool._CLIENTS.clear()

    asyncio.run(_run())


def test_close_all_reports_aclose_errors(monkeypatch) -> None:
    """close_all() should report aclose failures while still clearing the cache."""
    from zammad_pdf_archiver.adapters import redis_pool

    client = AsyncMock()
    client.aclose.side_effect = RuntimeError("connection lost")
    counter = FakeCounter()
    monkeypatch.setattr(redis_pool, "redis_pool_close_failures_total", counter)

    async def _run() -> None:
        redis_pool._CLIENTS.clear()
        redis_pool._CLIENTS["redis://broken"] = client
        try:
            close_failures = await redis_pool.close_all()
            check(not not close_failures == 1, "assertion failed")
            check(not not counter.count == 1, "assertion failed")
            check(not not len(redis_pool._CLIENTS) == 0, "assertion failed")
        finally:
            redis_pool._CLIENTS.clear()

    asyncio.run(_run())
