"""Verifies skipped in-flight deliveries remain eligible for later retry."""

from __future__ import annotations

import asyncio
from pathlib import Path

from chronikwerk.app.jobs import ticket_stores
from chronikwerk.app.jobs.process_ticket import process_ticket
from tests.support.process_ticket_helpers import (
    InflightRetryProcessTicketClient,
    flaky_then_successful_render,
    install_process_ticket_pipeline_doubles,
    process_ticket_payload,
    process_ticket_settings,
    run_concurrent_process_tickets,
)


def test_skipped_inflight_delivery_id_is_not_poisoned_for_retry(
    monkeypatch, tmp_path: Path
) -> None:
    ticket_stores.reset_for_tests()
    InflightRetryProcessTicketClient.reset()

    install_process_ticket_pipeline_doubles(
        monkeypatch,
        client_type=InflightRetryProcessTicketClient,
        render=flaky_then_successful_render(),
        tmp_path=tmp_path,
    )

    settings = process_ticket_settings(tmp_path)
    payload = process_ticket_payload()

    asyncio.run(run_concurrent_process_tickets(process_ticket, payload, settings))

    # Retry delivery d-2 after the in-flight run is over.
    asyncio.run(process_ticket("d-2", payload, settings))

    # Expected: first run writes one error note; retry run succeeds and writes one success note.
    assert InflightRetryProcessTicketClient._error_notes == 1
    assert InflightRetryProcessTicketClient._success_notes == 1
