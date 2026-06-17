from __future__ import annotations

import asyncio

from test.support.settings_factory import make_settings
from zammad_pdf_archiver.app.jobs import ticket_stores


def test_delivery_id_claims_once(tmp_path) -> None:
    settings = make_settings(str(tmp_path), overrides={"workflow": {"delivery_id_ttl_seconds": 60}})

    async def run() -> None:
        ticket_stores.reset_for_tests()
        assert await ticket_stores.try_claim_delivery_id(settings, "delivery-1") is True
        assert await ticket_stores.try_claim_delivery_id(settings, "delivery-1") is False

    asyncio.run(run())


def test_delivery_id_ttl_zero_disables_dedupe(tmp_path) -> None:
    settings = make_settings(str(tmp_path), overrides={"workflow": {"delivery_id_ttl_seconds": 0}})

    async def run() -> None:
        ticket_stores.reset_for_tests()
        assert await ticket_stores.try_claim_delivery_id(settings, "delivery-1") is True
        assert await ticket_stores.try_claim_delivery_id(settings, "delivery-1") is True

    asyncio.run(run())


def test_ticket_in_flight_is_process_local(tmp_path) -> None:
    settings = make_settings(str(tmp_path))

    async def run() -> None:
        ticket_stores.reset_for_tests()
        assert await ticket_stores.try_acquire_ticket(settings, 123) is True
        assert await ticket_stores.try_acquire_ticket(settings, 123) is False
        await ticket_stores.release_ticket(settings, 123)
        assert await ticket_stores.try_acquire_ticket(settings, 123) is True
        await ticket_stores.release_ticket(settings, 123)

    asyncio.run(run())
