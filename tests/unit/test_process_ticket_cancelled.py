"""Verifies cancellation bypasses error handling while restoring retryable tags."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

import chronikwerk.app.jobs.process_ticket as process_ticket_module
from chronikwerk.adapters.zammad.models import TagList
from chronikwerk.app.jobs import _ticket_pipeline as ticket_pipeline_module
from tests.support.settings_factory import make_settings


class _FakeClient:
    """Minimal async client used when cancellation occurs before Zammad operations."""

    def __init__(self, **kwargs) -> None:  # noqa: ANN003, ARG002
        pass

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
        return None


class _TagStateClient:
    """In-memory tag client used to verify cancellation leaves tags consistent."""

    def __init__(self) -> None:
        self.tags = {"pdf:sign"}

    async def get_ticket(self, _ticket_id: int) -> SimpleNamespace:
        return SimpleNamespace(id=1)

    async def list_tags(self, _ticket_id: int) -> TagList:
        return TagList(sorted(self.tags))

    async def remove_tag(self, _ticket_id: int, tag: str) -> None:
        self.tags.discard(tag)

    async def add_tag(self, _ticket_id: int, tag: str) -> None:
        self.tags.add(tag)


class _CancelledErrorFlowClient(_FakeClient):
    """Client double that records whether cancellation incorrectly enters the error flow."""

    last: _CancelledErrorFlowClient | None = None

    def __init__(self, **kwargs) -> None:  # noqa: ANN003, ARG002
        self.tags = {"pdf:processing"}
        type(self).last = self

    async def remove_tag(self, _ticket_id: int, tag: str) -> None:
        self.tags.discard(tag)

    async def add_tag(self, _ticket_id: int, tag: str) -> None:
        self.tags.add(tag)


def test_process_ticket_with_client_cancellation_does_not_run_error_flow(
    monkeypatch, tmp_path
) -> None:
    settings = make_settings(str(tmp_path))

    async def _cancelled_pipeline(_request):  # noqa: ANN001, ARG001
        raise asyncio.CancelledError()

    called = {"error_handler": 0}

    async def _error_handler(**kwargs):  # noqa: ANN003, ARG001
        called["error_handler"] += 1
        return process_ticket_module.ProcessTicketResult(
            status="failed_permanent",
            ticket_id=1,
            classification="Permanent",
            message="should-not-run",
        )

    monkeypatch.setattr(process_ticket_module, "AsyncZammadClient", _FakeClient)
    monkeypatch.setattr(process_ticket_module, "_run_ticket_pipeline", _cancelled_pipeline)
    monkeypatch.setattr(process_ticket_module, "_handle_ticket_pipeline_exception", _error_handler)

    ctx = process_ticket_module._TicketJobContext(  # noqa: SLF001
        settings=settings,
        ticket_id=1,
        delivery_id="delivery-1",
        request_id="req-1",
    )

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            process_ticket_module._process_ticket_with_client(  # noqa: SLF001
                ctx,
                payload={"ticket_id": 1},
            )
        )

    assert called["error_handler"] == 0


def test_pipeline_cancellation_restores_retryable_tag_state(monkeypatch, tmp_path) -> None:
    settings = make_settings(str(tmp_path))
    history: list[dict[str, object]] = []

    async def _cancelled_render(**_kwargs):
        raise asyncio.CancelledError()

    monkeypatch.setattr(
        ticket_pipeline_module,
        "resolve_storage_paths",
        lambda *_args, **_kwargs: (Path("ticket.pdf"), Path("ticket.pdf.json")),
    )
    monkeypatch.setattr(ticket_pipeline_module, "render_and_store_ticket", _cancelled_render)
    monkeypatch.setattr(
        ticket_pipeline_module,
        "record_history",
        lambda _ctx, **kwargs: history.append(kwargs),
    )
    ctx = process_ticket_module._TicketJobContext(  # noqa: SLF001
        settings=settings,
        ticket_id=1,
        delivery_id="delivery-1",
        request_id="req-1",
    )
    client = _TagStateClient()

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            ticket_pipeline_module.run_ticket_pipeline(
                ticket_pipeline_module.TicketPipelineRequest(
                    client=client,  # type: ignore[arg-type]
                    ctx=ctx,
                    payload={"ticket_id": 1},
                    options=ticket_pipeline_module._PipelineOptions(  # noqa: SLF001
                        trigger_tag="pdf:sign",
                        require_trigger_tag=True,
                        force_reprocess=False,
                    ),
                )
            )
        )

    assert client.tags == {"pdf:error", "pdf:sign"}
    assert history[-1] == {
        "status": "cancelled",
        "classification": "Transient",
        "message": "Processing cancelled; trigger restored for retry",
    }


def test_error_flow_cancellation_still_restores_retryable_tags(monkeypatch, tmp_path) -> None:
    settings = make_settings(str(tmp_path))

    async def _failed_pipeline(_request):  # noqa: ANN001, ARG001
        raise RuntimeError("render failed")

    async def _cancelled_error_handler(**_kwargs):
        raise asyncio.CancelledError()

    monkeypatch.setattr(
        process_ticket_module,
        "AsyncZammadClient",
        _CancelledErrorFlowClient,
    )
    monkeypatch.setattr(process_ticket_module, "_run_ticket_pipeline", _failed_pipeline)
    monkeypatch.setattr(
        process_ticket_module,
        "_handle_ticket_pipeline_exception",
        _cancelled_error_handler,
    )
    ctx = process_ticket_module._TicketJobContext(  # noqa: SLF001
        settings=settings,
        ticket_id=1,
        delivery_id="delivery-1",
        request_id="req-1",
    )

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            process_ticket_module._process_ticket_with_client(  # noqa: SLF001
                ctx,
                payload={"ticket_id": 1},
            )
        )

    client = _CancelledErrorFlowClient.last
    assert client is not None
    assert client.tags == {"pdf:error", "pdf:sign"}
