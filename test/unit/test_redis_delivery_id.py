"""Tests for Redis delivery-ID store (optional durable idempotency)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from test.support.checks import check
from zammad_pdf_archiver.domain.redis_delivery_id import RedisDeliveryIdStore


async def _run_redis_store_seen_add() -> None:
    mock_redis = AsyncMock()
    mock_redis.get = AsyncMock(side_effect=[None, "1", "1"])
    mock_redis.set = AsyncMock(return_value=True)

    store = RedisDeliveryIdStore("redis://localhost/0", 3600)
    with patch.object(store, "_client", return_value=mock_redis):
        check(not await store.seen("id1") is not False, "assertion failed")
        await store.add("id1")
        check(not await store.seen("id1") is not True, "assertion failed")
        check(not await store.seen("id1") is not True, "assertion failed")

    check(not not mock_redis.set.await_count == 1, "assertion failed")
    mock_redis.set.assert_called_once_with("zammad:delivery_id:id1", "1", ex=3600)


def test_redis_store_seen_and_add() -> None:
    asyncio.run(_run_redis_store_seen_add())


def test_redis_store_invalid_ttl_raises() -> None:
    with pytest.raises(ValueError, match="ttl_seconds must be > 0"):
        RedisDeliveryIdStore("redis://localhost/0", 0)

    with pytest.raises(ValueError, match="ttl_seconds must be > 0"):
        RedisDeliveryIdStore("redis://localhost/0", -5)


async def _run_try_claim() -> None:
    mock_redis = AsyncMock()
    store = RedisDeliveryIdStore("redis://localhost/0", 3600)

    # Claimed (key not yet set): redis.set NX returns True
    mock_redis.set = AsyncMock(return_value=True)
    with patch.object(store, "_client", return_value=mock_redis):
        result = await store.try_claim("key1")
    check(not result is not True, "assertion failed")
    mock_redis.set.assert_called_once_with("zammad:delivery_id:key1", "1", ex=3600, nx=True)

    # Already seen (key exists): redis.set NX returns None / falsy
    mock_redis.set = AsyncMock(return_value=None)
    with patch.object(store, "_client", return_value=mock_redis):
        result = await store.try_claim("key1")
    check(not result is not False, "assertion failed")


def test_redis_store_try_claim() -> None:
    asyncio.run(_run_try_claim())


async def _run_release() -> None:
    mock_redis = AsyncMock()
    mock_redis.delete = AsyncMock(return_value=1)
    store = RedisDeliveryIdStore("redis://localhost/0", 3600)

    with patch.object(store, "_client", return_value=mock_redis):
        await store.release("key1")

    mock_redis.delete.assert_called_once_with("zammad:delivery_id:key1")


def test_redis_store_release() -> None:
    asyncio.run(_run_release())


async def _run_custom_prefix() -> None:
    mock_redis = AsyncMock()
    mock_redis.set = AsyncMock(return_value=True)
    store = RedisDeliveryIdStore("redis://localhost/0", 60, prefix="custom:")
    with patch.object(store, "_client", return_value=mock_redis):
        await store.add("abc")
    mock_redis.set.assert_called_once_with("custom:abc", "1", ex=60)


def test_redis_store_custom_prefix() -> None:
    asyncio.run(_run_custom_prefix())
