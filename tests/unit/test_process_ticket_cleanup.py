"""Verifies successful processing avoids redundant tag-cleanup operations."""

from __future__ import annotations

import asyncio
from pathlib import Path

from chronikwerk.app.jobs import (
    _ticket_pipeline as ticket_pipeline_module,
)
from chronikwerk.app.jobs import (
    _ticket_pipeline_errors as ticket_pipeline_errors_module,
)
from chronikwerk.app.jobs import ticket_stores
from chronikwerk.app.jobs.process_ticket import process_ticket
from tests.support.process_ticket_helpers import (
    CapturingLog,
    CleanupProcessTicketClient,
    process_ticket_payload,
    process_ticket_settings,
    raise_transient_render_failure,
)


def test_process_ticket_does_not_do_redundant_processing_tag_cleanup(
    monkeypatch, tmp_path: Path
) -> None:
    ticket_stores.reset_for_tests()
    capturing_log = CapturingLog()
    monkeypatch.setattr(ticket_pipeline_errors_module, "log", capturing_log)
    monkeypatch.setattr(
        "chronikwerk.app.jobs.process_ticket.AsyncZammadClient",
        CleanupProcessTicketClient,
    )

    async def _no_op_apply_error(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr(
        ticket_pipeline_errors_module,
        "apply_error",
        _no_op_apply_error,
    )
    monkeypatch.setattr(
        ticket_pipeline_module,
        "build_and_render_pdf",
        raise_transient_render_failure,
    )

    settings = process_ticket_settings(tmp_path)
    payload = process_ticket_payload()

    asyncio.run(process_ticket("d-cleanup-log-1", payload, settings))

    assert "process_ticket.processing_tag_cleanup_failed" not in capturing_log.exception_events
