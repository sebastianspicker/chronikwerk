"""Unit tests for the redis_pool lazy-import helpers."""

from __future__ import annotations

import asyncio
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def test_import_redis_raises_when_not_installed() -> None:
    """import_redis() should raise RuntimeError when the redis package is absent."""
    with patch.dict(sys.modules, {"redis": None, "redis.asyncio": None, "redis.exceptions": None}):
        # Force re-import to hit the ImportError path
        from zammad_pdf_archiver.adapters import redis_pool

        with pytest.raises(RuntimeError, match="redis package"):
            redis_pool.import_redis()


def test_import_redis_class_returns_none_when_not_installed() -> None:
    """import_redis_class() should return None when the redis package is absent."""
    with patch.dict(sys.modules, {"redis": None, "redis.asyncio": None}):
        from zammad_pdf_archiver.adapters import redis_pool

        result = redis_pool.import_redis_class()
        assert result is None


def test_import_redis_succeeds_when_installed() -> None:
    """import_redis() should return (Redis, ResponseError) when redis is available."""
    try:
        import redis  # noqa: F401
    except ImportError:
        pytest.skip("redis package not installed")

    from zammad_pdf_archiver.adapters.redis_pool import import_redis

    Redis, ResponseError = import_redis()
    assert Redis is not None
    assert ResponseError is not None


def test_import_redis_class_returns_class_when_installed() -> None:
    """import_redis_class() should return the Redis class when installed."""
    try:
        import redis  # noqa: F401
    except ImportError:
        pytest.skip("redis package not installed")

    from zammad_pdf_archiver.adapters.redis_pool import import_redis_class

    cls = import_redis_class()
    assert cls is not None


# ---------------------------------------------------------------------------
# get_redis — caching, empty-URL guard
# ---------------------------------------------------------------------------


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
        # Clear cache to isolate test
        redis_pool._CLIENTS.clear()
        try:
            with patch.object(redis_pool, "import_redis", return_value=(fake_redis_cls, None)):
                c1 = await redis_pool.get_redis("redis://test:6379/0")
                c2 = await redis_pool.get_redis("redis://test:6379/0")
                assert c1 is c2
                # from_url should only be called once (cached)
                assert fake_redis_cls.from_url.call_count == 1
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
                assert c1 is not c2
                assert fake_redis_cls.from_url.call_count == 2
        finally:
            redis_pool._CLIENTS.clear()

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# close_all — closes cached clients and clears the cache
# ---------------------------------------------------------------------------


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
            await redis_pool.close_all()
            client1.aclose.assert_awaited_once()
            client2.aclose.assert_awaited_once()
            assert len(redis_pool._CLIENTS) == 0
        finally:
            redis_pool._CLIENTS.clear()

    asyncio.run(_run())


def test_close_all_tolerates_aclose_errors() -> None:
    """close_all() should not raise even if aclose() fails on a client."""
    from zammad_pdf_archiver.adapters import redis_pool

    client = AsyncMock()
    client.aclose.side_effect = RuntimeError("connection lost")

    async def _run() -> None:
        redis_pool._CLIENTS.clear()
        redis_pool._CLIENTS["redis://broken"] = client
        try:
            await redis_pool.close_all()  # should not raise
            assert len(redis_pool._CLIENTS) == 0
        finally:
            redis_pool._CLIENTS.clear()

    asyncio.run(_run())
