from __future__ import annotations

# pylint: disable=wrong-import-order
# ruff: noqa: I001  # Pylint and Ruff classify the in-repository test package differently.

import asyncio
from pathlib import Path

import zammad_pdf_archiver.app.jobs.process_ticket as process_ticket_module
from test.support.process_ticket_helpers import (
    CapturingLog,
    CleanupProcessTicketClient,
    no_op_apply_error,
    process_ticket_payload,
    process_ticket_settings,
    raise_transient_render_failure,
)
from zammad_pdf_archiver.app.jobs import ticket_stores
from zammad_pdf_archiver.app.jobs.process_ticket import process_ticket


def test_process_ticket_does_not_do_redundant_processing_tag_cleanup(
    monkeypatch, tmp_path: Path
) -> None:
    ticket_stores.reset_for_tests()
    capturing_log = CapturingLog()
    monkeypatch.setattr(process_ticket_module, "log", capturing_log)
    monkeypatch.setattr(
        "zammad_pdf_archiver.app.jobs.process_ticket.AsyncZammadClient",
        CleanupProcessTicketClient,
    )
    monkeypatch.setattr(
        "zammad_pdf_archiver.app.jobs.process_ticket.apply_error",
        no_op_apply_error,
    )
    monkeypatch.setattr(
        "zammad_pdf_archiver.app.jobs.process_ticket.build_and_render_pdf",
        raise_transient_render_failure,
    )

    settings = process_ticket_settings(tmp_path)
    payload = process_ticket_payload()

    asyncio.run(process_ticket("d-cleanup-log-1", payload, settings))

    assert "process_ticket.processing_tag_cleanup_failed" not in capturing_log.exception_events
