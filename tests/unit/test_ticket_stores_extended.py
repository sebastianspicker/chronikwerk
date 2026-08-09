"""Verifies delivery deduplication and process-local in-flight ticket tracking."""

from __future__ import annotations

import asyncio

from chronikwerk.app.jobs import ticket_stores
from tests.support.settings_factory import make_settings


async def _claim_delivery_twice(settings, expected: tuple[bool, bool]) -> None:
    """Assert delivery-id claim behavior for a configured TTL."""
    ticket_stores.reset_for_tests()
    assert await ticket_stores.try_claim_delivery_id(settings, "delivery-1") is expected[0]
    assert await ticket_stores.try_claim_delivery_id(settings, "delivery-1") is expected[1]


def test_delivery_id_claims_once(tmp_path) -> None:
    settings = make_settings(str(tmp_path), overrides={"workflow": {"delivery_id_ttl_seconds": 60}})

    asyncio.run(_claim_delivery_twice(settings, (True, False)))


def test_delivery_id_ttl_zero_disables_dedupe(tmp_path) -> None:
    settings = make_settings(str(tmp_path), overrides={"workflow": {"delivery_id_ttl_seconds": 0}})

    asyncio.run(_claim_delivery_twice(settings, (True, True)))


def test_ticket_in_flight_is_process_local(tmp_path) -> None:
    settings = make_settings(str(tmp_path))

    async def run() -> None:
        ticket_stores.reset_for_tests()
        assert await ticket_stores.try_acquire_ticket(settings, 123) is True
        assert await ticket_stores.try_acquire_ticket(settings, 123) is False
        await ticket_stores.release_ticket(123)
        assert await ticket_stores.try_acquire_ticket(settings, 123) is True
        await ticket_stores.release_ticket(123)

    asyncio.run(run())
