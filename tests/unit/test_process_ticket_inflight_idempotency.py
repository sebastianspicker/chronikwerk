"""Verifies skipped in-flight deliveries remain eligible for later retry."""

from __future__ import annotations

import asyncio
from pathlib import Path

from chronikwerk.app.jobs import (
    _ticket_pipeline as ticket_pipeline_module,
)
from chronikwerk.app.jobs import ticket_stores
from chronikwerk.app.jobs.process_ticket import process_ticket
from tests.support.process_ticket_helpers import (
    InflightRetryProcessTicketClient,
    fake_store_ticket_files,
    flaky_then_successful_render,
    process_ticket_payload,
    process_ticket_settings,
)


def test_skipped_inflight_delivery_id_is_not_poisoned_for_retry(
    monkeypatch, tmp_path: Path
) -> None:
    ticket_stores.reset_for_tests()
    InflightRetryProcessTicketClient.reset()

    monkeypatch.setattr(
        "chronikwerk.app.jobs.process_ticket.AsyncZammadClient",
        InflightRetryProcessTicketClient,
    )
    monkeypatch.setattr(
        ticket_pipeline_module,
        "build_and_render_pdf",
        flaky_then_successful_render(),
    )
    monkeypatch.setattr(
        ticket_pipeline_module,
        "store_ticket_files",
        fake_store_ticket_files(tmp_path),
    )

    settings = process_ticket_settings(tmp_path)
    payload = process_ticket_payload()

    async def _run_concurrent_once() -> None:
        await asyncio.gather(
            process_ticket("d-1", payload, settings),
            process_ticket("d-2", payload, settings),
        )

    asyncio.run(_run_concurrent_once())

    # Retry delivery d-2 after the in-flight run is over.
    asyncio.run(process_ticket("d-2", payload, settings))

    # Expected: first run writes one error note; retry run succeeds and writes one success note.
    assert InflightRetryProcessTicketClient._error_notes == 1
    assert InflightRetryProcessTicketClient._success_notes == 1
