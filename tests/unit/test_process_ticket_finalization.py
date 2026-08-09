"""Verifies finalization preserves durable success across retry and note failures."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock

import pytest

from chronikwerk.app.jobs import _ticket_pipeline as ticket_pipeline_module
from chronikwerk.app.jobs.ticket_storage import StorageResult


def _context() -> ticket_pipeline_module.TicketJobContext:
    """Build a deterministic context fixture for focused assertions."""
    settings = SimpleNamespace(
        workflow=SimpleNamespace(acknowledge_on_success=True),
    )
    return ticket_pipeline_module.TicketJobContext(
        settings=cast(Any, settings),
        ticket_id=123,
        delivery_id="delivery-123",
        request_id="request-123",
    )


async def _successful_retry(*_args, **_kwargs) -> None:
    """Complete finalization retry work without introducing a failure."""
    return None


def _storage_result() -> StorageResult:
    """Return the durable archive result used by finalization scenarios."""
    return StorageResult(
        target_path=Path("/archive/ticket.pdf"),
        sidecar_path=Path("/archive/ticket.pdf.json"),
        sha256_hex="abc123",
        size_bytes=42,
    )


def _assert_processed_history(processed: Mock, history: Mock) -> None:
    """Assert terminal success accounting and warning-to-success history."""
    processed.assert_called_once_with()
    assert [call.kwargs["status"] for call in history.call_args_list] == [
        "processed_with_warning",
        "processed",
    ]


def _assert_no_success_side_effects(
    client: SimpleNamespace, processed: Mock, history: Mock
) -> None:
    """Assert failed finalization does not report any terminal success signals."""
    client.create_internal_article.assert_not_awaited()
    processed.assert_not_called()
    history.assert_not_called()


def _assert_log_event(log_mock: Mock, event: str) -> None:
    """Assert the structured event emitted by finalization."""
    assert log_mock.call_args.args[0] == event


def test_apply_done_exhaustion_is_failure_without_processed_signals(monkeypatch) -> None:
    async def _fail_retry(*_args, **_kwargs) -> None:
        raise RuntimeError("zammad unavailable")

    client = SimpleNamespace(create_internal_article=AsyncMock())
    processed = Mock()
    history = Mock()
    log_exception = Mock()
    result = _storage_result()
    monkeypatch.setattr(ticket_pipeline_module, "async_retry", _fail_retry)
    monkeypatch.setattr(ticket_pipeline_module.processed_total, "inc", processed)
    monkeypatch.setattr(ticket_pipeline_module, "record_history", history)
    monkeypatch.setattr(ticket_pipeline_module.log, "exception", log_exception)

    with pytest.raises(RuntimeError, match="zammad unavailable"):
        asyncio.run(
            ticket_pipeline_module.finalize_success(
                client=cast(Any, client),
                ctx=_context(),
                trigger_tag="pdf:sign",
                now=datetime.now(UTC),
                storage_result=result,
            )
        )

    _assert_no_success_side_effects(client, processed, history)
    _assert_log_event(log_exception, "process_ticket.finalization_failed_after_storage")
    assert log_exception.call_args.kwargs["storage_succeeded"] is True
    assert log_exception.call_args.kwargs["storage_path"] == "/archive/ticket.pdf"


def test_success_note_failure_preserves_durable_success(monkeypatch) -> None:
    client = SimpleNamespace(
        create_internal_article=AsyncMock(side_effect=RuntimeError("note unavailable"))
    )
    processed = Mock()
    history = Mock()
    log_exception = Mock()
    result = _storage_result()
    monkeypatch.setattr(ticket_pipeline_module, "async_retry", _successful_retry)
    monkeypatch.setattr(ticket_pipeline_module.processed_total, "inc", processed)
    monkeypatch.setattr(ticket_pipeline_module, "record_history", history)
    monkeypatch.setattr(ticket_pipeline_module.log, "exception", log_exception)

    asyncio.run(
        ticket_pipeline_module.finalize_success(
            client=cast(Any, client),
            ctx=_context(),
            trigger_tag="pdf:sign",
            now=datetime.now(UTC),
            storage_result=result,
        )
    )

    client.create_internal_article.assert_awaited_once()
    _assert_processed_history(processed, history)
    _assert_log_event(log_exception, "process_ticket.success_note_failed_after_completion")


def test_success_note_cancellation_preserves_terminal_success(monkeypatch) -> None:
    client = SimpleNamespace(
        create_internal_article=AsyncMock(side_effect=asyncio.CancelledError())
    )
    processed = Mock()
    history = Mock()
    log_warning = Mock()
    result = _storage_result()
    monkeypatch.setattr(ticket_pipeline_module, "async_retry", _successful_retry)
    monkeypatch.setattr(ticket_pipeline_module.processed_total, "inc", processed)
    monkeypatch.setattr(ticket_pipeline_module, "record_history", history)
    monkeypatch.setattr(ticket_pipeline_module.log, "warning", log_warning)

    with pytest.raises(ticket_pipeline_module.CompletedTicketCancellation):
        asyncio.run(
            ticket_pipeline_module.finalize_success(
                client=cast(Any, client),
                ctx=_context(),
                trigger_tag="pdf:sign",
                now=datetime.now(UTC),
                storage_result=result,
            )
        )

    _assert_processed_history(processed, history)
    _assert_log_event(log_warning, "process_ticket.success_note_cancelled_after_completion")
