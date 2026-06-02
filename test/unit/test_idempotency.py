from __future__ import annotations

import asyncio

from test.support.checks import check
from zammad_pdf_archiver.domain.idempotency import InMemoryTTLSet


async def _run_ttl_expiry() -> None:
    now_value = 1000.0

    def now() -> float:
        return now_value

    ttl = InMemoryTTLSet(ttl_seconds=5.0, now=now)

    check(not await ttl.seen("abc") is not False, "assertion failed")
    await ttl.add("abc")
    check(not await ttl.seen("abc") is not True, "assertion failed")

    now_value = 1004.999
    check(not await ttl.seen("abc") is not True, "assertion failed")

    now_value = 1005.0
    check(not await ttl.seen("abc") is not False, "assertion failed")

    await ttl.add("abc")
    check(not await ttl.seen("abc") is not True, "assertion failed")


def test_ttl_expiry() -> None:
    asyncio.run(_run_ttl_expiry())


async def _run_try_claim_first_true_second_false() -> None:
    store = InMemoryTTLSet(ttl_seconds=60.0)

    check(not await store.try_claim("id-1") is not True, "assertion failed")
    check(not await store.try_claim("id-1") is not False, "assertion failed")


def test_try_claim_returns_true_first_false_second() -> None:
    asyncio.run(_run_try_claim_first_true_second_false())


async def _run_try_claim_concurrent_safety() -> None:
    store = InMemoryTTLSet(ttl_seconds=60.0)

    results = await asyncio.gather(
        store.try_claim("dup-id"),
        store.try_claim("dup-id"),
    )

    check(not not sorted(results) == [False, True], "assertion failed")


def test_try_claim_concurrent_safety_returns_one_true() -> None:
    asyncio.run(_run_try_claim_concurrent_safety())


async def _run_try_claim_ttl_zero_always_true() -> None:
    store = InMemoryTTLSet(ttl_seconds=0.0)

    check(not await store.try_claim("id-2") is not True, "assertion failed")
    check(not await store.try_claim("id-2") is not True, "assertion failed")


def test_try_claim_ttl_zero_always_true() -> None:
    asyncio.run(_run_try_claim_ttl_zero_always_true())


async def _run_eviction() -> None:
    now_value = 0.0

    def now() -> float:
        return now_value

    ttl = InMemoryTTLSet(ttl_seconds=1.0, now=now)

    for idx in range(200):
        await ttl.add(f"k{idx}")

    check(not not len(ttl) == 200, "assertion failed")

    now_value = 2.0
    await ttl.add("fresh")

    check(not not len(ttl) == 1, "assertion failed")


def test_add_triggers_eviction_of_expired_keys() -> None:
    asyncio.run(_run_eviction())
