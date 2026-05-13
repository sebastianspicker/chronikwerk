"""Shared Redis connection pool for all modules that need async Redis access.

Consolidates lazy import, client creation, and connection reuse across:
- redis_queue.py (queue worker)
- history.py (job history stream)
- redis_delivery_id.py (durable idempotency)

Usage::

    from zammad_pdf_archiver.adapters.redis_pool import get_redis, import_redis

    Redis, ResponseError = import_redis()  # raises RuntimeError if not installed
    client = await get_redis(redis_url)    # cached async client
"""

from __future__ import annotations

import asyncio
from typing import Any

import structlog

log = structlog.get_logger(__name__)

_LOCK = asyncio.Lock()
_CLIENTS: dict[str, Any] = {}


def import_redis() -> tuple[Any, Any]:
    """Lazily import redis.asyncio and return (Redis, ResponseError).

    Raises RuntimeError if the ``redis`` package is not installed.
    """
    try:
        from redis.asyncio import Redis
        from redis.exceptions import ResponseError
    except ImportError as exc:
        raise RuntimeError(
            "Redis backend requires the redis package. "
            "Install with: pip install zammad-pdf-archiver[redis]"
        ) from exc
    return Redis, ResponseError


def import_redis_class() -> Any:
    """Return ``redis.asyncio.Redis`` or ``None`` if not installed."""
    try:
        from redis.asyncio import Redis
    except ImportError:
        return None
    return Redis


async def get_redis(redis_url: str) -> Any:
    """Return a cached async Redis client for the given URL.

    Creates a new client on first call for each URL, then reuses it.
    Thread-safe via asyncio lock.
    """
    if not redis_url or not redis_url.strip():
        raise RuntimeError("redis_url is required")

    async with _LOCK:
        cached = _CLIENTS.get(redis_url)
        if cached is not None:
            return cached

        Redis, _ = import_redis()
        client = Redis.from_url(
            redis_url,
            decode_responses=True,
            socket_timeout=5.0,
            socket_connect_timeout=5.0,
        )
        _CLIENTS[redis_url] = client
        return client


async def close_all() -> None:
    """Close all cached Redis clients. Safe to call during shutdown."""
    async with _LOCK:
        for redis_url, client in _CLIENTS.items():
            try:
                await client.aclose()
            except Exception:
                log.warning("redis_pool.close_failed", redis_url=redis_url, exc_info=True)
        _CLIENTS.clear()
