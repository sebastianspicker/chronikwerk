"""Redis-backed delivery-ID store for durable idempotency.
Requires optional dependency: pip install zammad-pdf-archiver[redis]."""

from __future__ import annotations

from typing import Any

from zammad_pdf_archiver.adapters.redis_pool import get_redis

_REDIS_PREFIX = "zammad:delivery_id:"


class RedisDeliveryIdStore:
    """DeliveryIdStore implementation using Redis with TTL."""

    def __init__(
        self,
        redis_url: str,
        ttl_seconds: int,
        prefix: str = "zammad:delivery_id:",
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be > 0 for Redis store")
        self._redis_url = redis_url
        self._ttl_seconds = ttl_seconds
        self._prefix = prefix

    async def _client(self) -> Any:
        return await get_redis(self._redis_url)

    def _key(self, key: str) -> str:
        return self._prefix + key

    async def seen(self, key: str) -> bool:
        redis = await self._client()
        value = await redis.get(self._key(key))
        return value is not None

    async def add(self, key: str) -> None:
        redis = await self._client()
        await redis.set(self._key(key), "1", ex=self._ttl_seconds)

    async def try_claim(self, key: str) -> bool:
        """Atomically claim key (SET NX EX). True if claimed, False if seen."""
        redis = await self._client()
        full_key = self._key(key)
        # SET key "1" NX EX ttl: set only if not exists, with TTL; return True if key was set.
        return bool(await redis.set(full_key, "1", ex=self._ttl_seconds, nx=True))

    async def release(self, key: str) -> None:
        """Release a claimed key (delete it)."""
        redis = await self._client()
        await redis.delete(self._key(key))

    async def aclose(self) -> None:
        """No-op — connections are managed by the shared redis_pool."""
