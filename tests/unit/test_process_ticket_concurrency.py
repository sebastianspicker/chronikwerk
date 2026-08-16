"""Verifies concurrent work for one ticket is serialized."""

from __future__ import annotations

import asyncio
from pathlib import Path

from chronikwerk.app.jobs import ticket_stores
from chronikwerk.app.jobs.process_ticket import process_ticket
from tests.support.process_ticket_helpers import (
    SerializingProcessTicketClient,
    install_process_ticket_pipeline_doubles,
    process_ticket_payload,
    process_ticket_settings,
    run_concurrent_process_tickets,
    successful_pipeline_render,
)


def test_process_ticket_serializes_same_ticket_concurrent_runs(monkeypatch, tmp_path: Path) -> None:
    ticket_stores.reset_for_tests()
    SerializingProcessTicketClient.reset()

    install_process_ticket_pipeline_doubles(
        monkeypatch,
        client_type=SerializingProcessTicketClient,
        render=successful_pipeline_render,
        tmp_path=tmp_path,
    )

    settings = process_ticket_settings(tmp_path)
    payload = process_ticket_payload(123)

    asyncio.run(run_concurrent_process_tickets(process_ticket, payload, settings))

    assert SerializingProcessTicketClient._notes_written == 1
