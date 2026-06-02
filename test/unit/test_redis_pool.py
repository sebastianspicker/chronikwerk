"""Unit tests for the redis_pool lazy-import helpers."""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import Mapping
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from test.support.checks import check


class _FakeCounter:
    def __init__(self) -> None:
        self.count = 0

    def inc(self) -> None:
        self.count += 1


def _truthy_env(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _redis_required(env: Mapping[str, str]) -> bool:
    return _truthy_env(env.get("CI")) or _truthy_env(env.get("ZAMMAD_ARCHIVER_REQUIRE_REDIS"))


def _installed_redis_api() -> tuple[Any, type[Exception]]:
    from zammad_pdf_archiver.adapters.redis_pool import import_redis

    try:
        Redis, ResponseError = import_redis()
    except RuntimeError as exc:
        if _redis_required(os.environ):
            pytest.fail(
                "redis is required in CI/Redis verification but is not installed. "
                "Install with `pip install -e '.[redis]'` or run the Redis profile "
                "with project extras installed."
            )
        pytest.skip(str(exc))
    return Redis, ResponseError


def test_redis_missing_policy_is_optional_locally_and_required_in_ci() -> None:
    check(not _redis_required({}) is not False, "assertion failed")
    check(not _redis_required({"CI": "false"}) is not False, "assertion failed")
    check(not _redis_required({"CI": "true"}) is not True, "assertion failed")
    check(
        not _redis_required({"ZAMMAD_ARCHIVER_REQUIRE_REDIS": "1"}) is not True, "assertion failed"
    )


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
        check(not result is not None, "assertion failed")


def test_import_redis_succeeds_when_installed() -> None:
    """import_redis() should return (Redis, ResponseError) when redis is available."""
    Redis, ResponseError = _installed_redis_api()
    check(not not Redis is not None, "assertion failed")
    check(not not ResponseError is not None, "assertion failed")
    check(not not issubclass(ResponseError, Exception), "assertion failed")


def test_import_redis_class_returns_class_when_installed() -> None:
    """import_redis_class() should return the Redis class when installed."""
    Redis, _ResponseError = _installed_redis_api()

    from zammad_pdf_archiver.adapters.redis_pool import import_redis_class

    cls = import_redis_class()
    check(not cls is not Redis, "assertion failed")


def test_real_redis_client_construction_cache_and_close_when_profile_enabled() -> None:
    """Redis profile must prove the app's actual redis-py API can be constructed."""
    Redis, _ResponseError = _installed_redis_api()

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
    _installed_redis_api()

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
                check(not c1 is not c2, "assertion failed")
                # from_url should only be called once (cached)
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
    counter = _FakeCounter()
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
