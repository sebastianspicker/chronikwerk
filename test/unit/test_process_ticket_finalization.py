from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock

import pytest

from zammad_pdf_archiver.app.jobs import process_ticket as process_ticket_module
from zammad_pdf_archiver.app.jobs.ticket_storage import StorageResult


def _context() -> process_ticket_module._TicketJobContext:
    settings = SimpleNamespace(
        workflow=SimpleNamespace(acknowledge_on_success=True),
    )
    return process_ticket_module._TicketJobContext(
        settings=cast(Any, settings),
        ticket_id=123,
        delivery_id="delivery-123",
        request_id="request-123",
    )


def test_apply_done_exhaustion_is_failure_without_processed_signals(monkeypatch) -> None:
    async def _fail_retry(*_args, **_kwargs) -> None:
        raise RuntimeError("zammad unavailable")

    client = SimpleNamespace(create_internal_article=AsyncMock())
    processed = Mock()
    history = Mock()
    log_exception = Mock()
    result = StorageResult(
        target_path=Path("/archive/ticket.pdf"),
        sidecar_path=Path("/archive/ticket.pdf.json"),
        sha256_hex="abc123",
        size_bytes=42,
    )
    monkeypatch.setattr(process_ticket_module, "async_retry", _fail_retry)
    monkeypatch.setattr(process_ticket_module.processed_total, "inc", processed)
    monkeypatch.setattr(process_ticket_module, "_record_history", history)
    monkeypatch.setattr(process_ticket_module.log, "exception", log_exception)

    with pytest.raises(RuntimeError, match="zammad unavailable"):
        asyncio.run(
            process_ticket_module._finalize_success(
                client=cast(Any, client),
                ctx=_context(),
                trigger_tag="pdf:sign",
                now=datetime.now(UTC),
                storage_result=result,
            )
        )

    client.create_internal_article.assert_not_awaited()
    processed.assert_not_called()
    history.assert_not_called()
    assert log_exception.call_args.args[0] == (
        "process_ticket.finalization_failed_after_storage"
    )
    assert log_exception.call_args.kwargs["storage_succeeded"] is True
    assert log_exception.call_args.kwargs["storage_path"] == "/archive/ticket.pdf"


def test_success_note_failure_preserves_durable_success(monkeypatch) -> None:
    async def _successful_retry(*_args, **_kwargs) -> None:
        return None

    client = SimpleNamespace(
        create_internal_article=AsyncMock(side_effect=RuntimeError("note unavailable"))
    )
    processed = Mock()
    history = Mock()
    log_exception = Mock()
    result = StorageResult(
        target_path=Path("/archive/ticket.pdf"),
        sidecar_path=Path("/archive/ticket.pdf.json"),
        sha256_hex="abc123",
        size_bytes=42,
    )
    monkeypatch.setattr(process_ticket_module, "async_retry", _successful_retry)
    monkeypatch.setattr(process_ticket_module.processed_total, "inc", processed)
    monkeypatch.setattr(process_ticket_module, "_record_history", history)
    monkeypatch.setattr(process_ticket_module.log, "exception", log_exception)

    asyncio.run(
        process_ticket_module._finalize_success(
            client=cast(Any, client),
            ctx=_context(),
            trigger_tag="pdf:sign",
            now=datetime.now(UTC),
            storage_result=result,
        )
    )

    client.create_internal_article.assert_awaited_once()
    processed.assert_called_once_with()
    assert [call.kwargs["status"] for call in history.call_args_list] == [
        "processed_with_warning",
        "processed",
    ]
    assert log_exception.call_args.args[0] == (
        "process_ticket.success_note_failed_after_completion"
    )


def test_success_note_cancellation_preserves_terminal_success(monkeypatch) -> None:
    async def _successful_retry(*_args, **_kwargs) -> None:
        return None

    client = SimpleNamespace(
        create_internal_article=AsyncMock(side_effect=asyncio.CancelledError())
    )
    processed = Mock()
    history = Mock()
    log_warning = Mock()
    result = StorageResult(
        target_path=Path("/archive/ticket.pdf"),
        sidecar_path=Path("/archive/ticket.pdf.json"),
        sha256_hex="abc123",
        size_bytes=42,
    )
    monkeypatch.setattr(process_ticket_module, "async_retry", _successful_retry)
    monkeypatch.setattr(process_ticket_module.processed_total, "inc", processed)
    monkeypatch.setattr(process_ticket_module, "_record_history", history)
    monkeypatch.setattr(process_ticket_module.log, "warning", log_warning)

    with pytest.raises(process_ticket_module._CompletedTicketCancellation):  # noqa: SLF001
        asyncio.run(
            process_ticket_module._finalize_success(
                client=cast(Any, client),
                ctx=_context(),
                trigger_tag="pdf:sign",
                now=datetime.now(UTC),
                storage_result=result,
            )
        )

    processed.assert_called_once_with()
    assert [call.kwargs["status"] for call in history.call_args_list] == [
        "processed_with_warning",
        "processed",
    ]
    assert log_warning.call_args.args[0] == (
        "process_ticket.success_note_cancelled_after_completion"
    )
