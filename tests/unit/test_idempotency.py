"""Verifies idempotency TTL expiry, eviction, and fail-closed capacity handling."""

from __future__ import annotations

import asyncio

from chronikwerk.domain.idempotency import InMemoryTTLSet


async def _run_ttl_expiry() -> None:
    """Execute the ttl expiry scenario with controlled timing."""
    now_value = 1000.0

    def now() -> float:
        return now_value

    ttl = InMemoryTTLSet(ttl_seconds=5.0, now=now)

    assert await ttl.seen("abc") is False
    await ttl.add("abc")
    assert await ttl.seen("abc") is True

    now_value = 1004.999
    assert await ttl.seen("abc") is True

    now_value = 1005.0
    assert await ttl.seen("abc") is False

    await ttl.add("abc")
    assert await ttl.seen("abc") is True


def test_ttl_expiry() -> None:
    asyncio.run(_run_ttl_expiry())


async def _run_eviction() -> None:
    """Execute the eviction scenario with controlled timing."""
    now_value = 0.0

    def now() -> float:
        return now_value

    ttl = InMemoryTTLSet(ttl_seconds=1.0, now=now)

    for idx in range(200):
        await ttl.add(f"k{idx}")

    assert len(ttl) == 200

    now_value = 2.0
    await ttl.add("fresh")

    assert len(ttl) == 1


def test_add_triggers_eviction_of_expired_keys() -> None:
    asyncio.run(_run_eviction())


async def _run_capacity_bound() -> None:
    """Execute the capacity bound scenario with controlled timing."""
    now_value = 0.0

    def now() -> float:
        return now_value

    ttl = InMemoryTTLSet(ttl_seconds=5.0, now=now, max_entries=2)

    assert await ttl.try_claim("first") is True
    assert await ttl.try_claim("second") is True
    assert await ttl.try_claim("third") is False
    assert len(ttl) == 2

    now_value = 5.0
    assert await ttl.try_claim("third") is True
    assert len(ttl) == 1


def test_capacity_fails_closed_after_expiring_stale_keys() -> None:
    asyncio.run(_run_capacity_bound())
