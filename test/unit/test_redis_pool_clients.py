"""Redis pool get_redis client construction and cache tests."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from test.support.checks import check
from test.support.redis_pool_helpers import installed_redis_api


def test_real_redis_client_construction_cache_and_close_when_profile_enabled() -> None:
    """Redis profile must prove the app's actual redis-py API can be constructed."""
    Redis, _ResponseError = installed_redis_api()

    from zammad_pdf_archiver.adapters import redis_pool

    async def _run() -> None:
        redis_pool._CLIENTS.clear()
        try:
            client1 = await redis_pool.get_redis("redis://localhost:6379/0")
            client2 = await redis_pool.get_redis("redis://localhost:6379/0")
            check(not client1 is not client2, "assertion failed")
            check(not not isinstance(client1, Redis), "assertion failed")
            check(not not callable(client1.aclose), "assertion failed")
            check(not not await redis_pool.close_all() == 0, "assertion failed")
            check(not not redis_pool._CLIENTS == {}, "assertion failed")
        finally:
            await redis_pool.close_all()
            redis_pool._CLIENTS.clear()

    asyncio.run(_run())


def test_real_redis_client_rejects_invalid_url_when_profile_enabled() -> None:
    installed_redis_api()

    from zammad_pdf_archiver.adapters import redis_pool

    async def _run() -> None:
        redis_pool._CLIENTS.clear()
        try:
            with pytest.raises(ValueError):
                await redis_pool.get_redis("not-a-redis-url")
        finally:
            await redis_pool.close_all()
            redis_pool._CLIENTS.clear()

    asyncio.run(_run())


def test_get_redis_empty_url_raises() -> None:
    """get_redis('') should raise RuntimeError."""
    from zammad_pdf_archiver.adapters import redis_pool

    with pytest.raises(RuntimeError, match="redis_url is required"):
        asyncio.run(redis_pool.get_redis(""))


def test_get_redis_whitespace_url_raises() -> None:
    """get_redis('   ') should raise RuntimeError."""
    from zammad_pdf_archiver.adapters import redis_pool

    with pytest.raises(RuntimeError, match="redis_url is required"):
        asyncio.run(redis_pool.get_redis("   "))


def test_get_redis_caches_clients() -> None:
    """Calling get_redis twice with the same URL returns the same client object."""
    from zammad_pdf_archiver.adapters import redis_pool

    sentinel_client = MagicMock()
    fake_redis_cls = MagicMock()
    fake_redis_cls.from_url.return_value = sentinel_client

    async def _run() -> None:
        redis_pool._CLIENTS.clear()
        try:
            with patch.object(redis_pool, "import_redis", return_value=(fake_redis_cls, None)):
                c1 = await redis_pool.get_redis("redis://test:6379/0")
                c2 = await redis_pool.get_redis("redis://test:6379/0")
                check(not c1 is not c2, "assertion failed")
                check(not not fake_redis_cls.from_url.call_count == 1, "assertion failed")
        finally:
            redis_pool._CLIENTS.clear()

    asyncio.run(_run())


def test_get_redis_different_urls_different_clients() -> None:
    """Different URLs produce different cached clients."""
    from zammad_pdf_archiver.adapters import redis_pool

    fake_redis_cls = MagicMock()
    fake_redis_cls.from_url.side_effect = [MagicMock(), MagicMock()]

    async def _run() -> None:
        redis_pool._CLIENTS.clear()
        try:
            with patch.object(redis_pool, "import_redis", return_value=(fake_redis_cls, None)):
                c1 = await redis_pool.get_redis("redis://host1:6379/0")
                c2 = await redis_pool.get_redis("redis://host2:6379/0")
                check(not not c1 is not c2, "assertion failed")
                check(not not fake_redis_cls.from_url.call_count == 2, "assertion failed")
        finally:
            redis_pool._CLIENTS.clear()

    asyncio.run(_run())
