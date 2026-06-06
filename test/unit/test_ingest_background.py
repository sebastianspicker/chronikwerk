from __future__ import annotations

import asyncio

from starlette.requests import Request

from test.support.checks import check
from test.support.logging_helpers import CapturingWarningLog as _CapturingLog
from test.support.settings_factory import make_settings
from zammad_pdf_archiver.app.jobs.process_ticket import ProcessTicketResult
from zammad_pdf_archiver.app.routes.ingest import IngestPayload
from zammad_pdf_archiver.app.server import create_app
from zammad_pdf_archiver.config.settings import Settings


def _test_settings(storage_root: str) -> Settings:
    return make_settings(storage_root)


def test_ingest_does_not_block_on_processing(tmp_path, monkeypatch) -> None:
    """
    Regression: docs/architecture promise that POST /ingest returns 202 immediately and does
    not wait for the full processing pipeline (PDF render/sign/storage/Zammad updates).
    """
    import zammad_pdf_archiver.app.routes.ingest as ingest_route

    called: list[object] = []
    gate = asyncio.Event()

    async def _slow_process_ticket(delivery_id, payload, settings) -> None:
        called.append((delivery_id, payload, settings))
        await gate.wait()

    monkeypatch.setattr(ingest_route, "process_ticket", _slow_process_ticket)

    app = create_app(_test_settings(str(tmp_path)))
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/ingest",
        "headers": [(b"x-zammad-delivery", b"delivery-bg-1")],
        "app": app,
    }
    request = Request(scope)
    request.state.request_id = "req-bg-1"

    async def _call() -> None:
        payload = IngestPayload.model_validate({"ticket": {"id": 123}})
        response = await ingest_route.ingest_webhook(request, payload)
        check(not not response.status_code == 202, "assertion failed")

    # If /ingest awaited the job, this would time out.
    asyncio.run(asyncio.wait_for(_call(), timeout=0.2))
    # We don't strictly assert called == [] here because asyncio.create_task
    # might have started the task before _call returned.
    # The lack of timeout is the primary proof of non-blocking behavior.


def test_ingest_background_logs_lock_release_failure(tmp_path, monkeypatch) -> None:
    import zammad_pdf_archiver.app.routes.ingest as ingest_route

    capturing_log = _CapturingLog()

    async def _stub_process_ticket(*args, **kwargs) -> ProcessTicketResult:  # noqa: ANN002, ANN003
        return ProcessTicketResult(
            status="processed",
            ticket_id=123,
            lock_release_failed=True,
        )

    monkeypatch.setattr(ingest_route, "process_ticket", _stub_process_ticket)
    monkeypatch.setattr(ingest_route, "log", capturing_log)

    asyncio.run(
        ingest_route._run_process_ticket_background(  # noqa: SLF001
            delivery_id="delivery-lock-1",
            payload={"ticket": {"id": 123}},
            settings=_test_settings(str(tmp_path)),
        )
    )

    check(
        not not capturing_log.warning_events
        == [
            (
                "ingest.ticket_lock_release_failed",
                {"ticket_id": 123, "delivery_id": "delivery-lock-1"},
            )
        ],
        "assertion failed",
    )


def test_ingest_background_counts_and_logs_history_record_failure(
    tmp_path,
    monkeypatch,
) -> None:
    import zammad_pdf_archiver.app.routes.ingest as ingest_route

    class _Counter:
        def __init__(self) -> None:
            self.count = 0

        def inc(self) -> None:
            self.count += 1

    failed_counter = _Counter()
    capturing_log = _CapturingLog()

    async def _stub_process_ticket(*args, **kwargs) -> ProcessTicketResult:  # noqa: ANN002, ANN003
        return ProcessTicketResult(
            status="processed",
            ticket_id=123,
            history_recorded=False,
        )

    monkeypatch.setattr(ingest_route, "process_ticket", _stub_process_ticket)
    monkeypatch.setattr(ingest_route, "history_record_failed_total", failed_counter)
    monkeypatch.setattr(ingest_route, "log", capturing_log)

    asyncio.run(
        ingest_route._run_process_ticket_background(  # noqa: SLF001
            delivery_id="delivery-history-1",
            payload={"ticket": {"id": 123}},
            settings=_test_settings(str(tmp_path)),
        )
    )

    check(not not failed_counter.count == 1, "assertion failed")
    check(
        not not capturing_log.warning_events
        == [
            (
                "process_ticket.history_not_recorded",
                {"ticket_id": 123, "delivery_id": "delivery-history-1"},
            )
        ],
        "assertion failed",
    )
