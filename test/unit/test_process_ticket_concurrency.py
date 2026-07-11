from __future__ import annotations

import asyncio
from pathlib import Path

from test.support.process_ticket_helpers import (
    SerializingProcessTicketClient,
    fake_store_ticket_files,
    process_ticket_payload,
    process_ticket_settings,
    successful_render,
)
from zammad_pdf_archiver.app.jobs import ticket_stores
from zammad_pdf_archiver.app.jobs.process_ticket import process_ticket


def test_process_ticket_serializes_same_ticket_concurrent_runs(
    monkeypatch, tmp_path: Path
) -> None:
    ticket_stores.reset_for_tests()
    SerializingProcessTicketClient.reset()

    monkeypatch.setattr(
        "zammad_pdf_archiver.app.jobs.process_ticket.AsyncZammadClient",
        SerializingProcessTicketClient,
    )
    monkeypatch.setattr(
        "zammad_pdf_archiver.app.jobs.process_ticket.build_and_render_pdf",
        successful_render,
    )
    monkeypatch.setattr(
        "zammad_pdf_archiver.app.jobs.process_ticket.store_ticket_files",
        fake_store_ticket_files(tmp_path),
    )

    settings = process_ticket_settings(tmp_path)
    payload = process_ticket_payload(123)

    async def _run() -> None:
        await asyncio.gather(
            process_ticket("d-1", payload, settings),
            process_ticket("d-2", payload, settings),
        )

    asyncio.run(_run())

    assert SerializingProcessTicketClient._notes_written == 1
