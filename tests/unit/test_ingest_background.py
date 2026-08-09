"""Verifies ingest schedules processing without blocking the HTTP response."""

from __future__ import annotations

import asyncio

import pytest
from starlette.requests import Request

from chronikwerk.app.jobs.admission import JobAdmission
from chronikwerk.app.routes.ingest import IngestPayload
from chronikwerk.app.server import create_app
from tests.support.settings_factory import make_settings


def test_ingest_does_not_block_on_processing(tmp_path, monkeypatch) -> None:
    """
    Regression: docs/architecture promise that POST /ingest returns 202 immediately and does
    not wait for the full processing pipeline (PDF render/sign/storage/Zammad updates).
    """
    import chronikwerk.app.routes.ingest as ingest_route

    called: list[object] = []
    gate = asyncio.Event()

    async def _slow_process_ticket(delivery_id, payload, settings) -> None:
        called.append((delivery_id, payload, settings))
        await gate.wait()

    monkeypatch.setattr(ingest_route, "process_ticket", _slow_process_ticket)

    app = create_app(make_settings(str(tmp_path)))
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
        assert response.status_code == 202

    # If /ingest awaited the job, this would time out.
    asyncio.run(asyncio.wait_for(_call(), timeout=0.2))
    # We don't strictly assert called == [] here because asyncio.create_task
    # might have started the task before _call returned.
    # The lack of timeout is the primary proof of non-blocking behavior.


def test_background_runner_releases_admission_after_processor_failure(
    tmp_path, monkeypatch
) -> None:
    import chronikwerk.app.routes.ingest as ingest_route

    async def failed_processing(*_args: object) -> None:
        raise RuntimeError("upstream failed")

    monkeypatch.setattr(ingest_route, "process_ticket", failed_processing)

    async def run() -> None:
        admission = JobAdmission(max_pending=1, max_running=1)
        assert admission.try_reserve()
        await ingest_route._run_process_ticket_background(  # noqa: SLF001
            delivery_id="delivery-1",
            payload={"ticket_id": 123},
            settings=make_settings(str(tmp_path)),
            admission=admission,
        )
        assert admission.pending == 0
        assert admission.running == 0

    asyncio.run(run())


def test_background_runner_cancels_reservation_for_invalid_payload_or_shutdown(
    tmp_path, monkeypatch
) -> None:
    import chronikwerk.app.routes.ingest as ingest_route

    async def run() -> None:
        invalid_admission = JobAdmission(max_pending=1, max_running=1)
        assert invalid_admission.try_reserve()
        await ingest_route._run_process_ticket_background(  # noqa: SLF001
            delivery_id=None,
            payload={},
            settings=make_settings(str(tmp_path)),
            admission=invalid_admission,
        )
        assert invalid_admission.pending == 0

        closing_admission = JobAdmission(max_pending=1, max_running=1)
        assert closing_admission.try_reserve()
        await closing_admission.close()
        await ingest_route._run_process_ticket_background(  # noqa: SLF001
            delivery_id=None,
            payload={"ticket_id": 124},
            settings=make_settings(str(tmp_path)),
            admission=closing_admission,
        )
        assert closing_admission.pending == 0
        assert closing_admission.running == 0

    asyncio.run(run())


def test_background_scheduler_returns_reservation_if_task_creation_fails(
    tmp_path, monkeypatch
) -> None:
    import chronikwerk.app.routes.ingest as ingest_route

    admission = JobAdmission(max_pending=0, max_running=1)
    monkeypatch.setattr(ingest_route, "is_shutting_down", lambda: False)
    monkeypatch.setattr(
        ingest_route,
        "_create_background_task",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("task creation failed")),
    )

    with pytest.raises(RuntimeError, match="task creation failed"):
        ingest_route._schedule_background_task(  # noqa: SLF001
            delivery_id=None,
            payload={"ticket_id": 123},
            settings=make_settings(str(tmp_path)),
            admission=admission,
        )

    assert admission.pending == 0


def test_capacity_response_tells_clients_when_to_retry() -> None:
    import chronikwerk.app.routes.ingest as ingest_route

    response = ingest_route._overload_error()  # noqa: SLF001

    assert response.status_code == 503
    assert response.headers["Retry-After"] == "1"


def test_background_scheduler_refuses_shutdown_and_full_admission(tmp_path, monkeypatch) -> None:
    import chronikwerk.app.routes.ingest as ingest_route

    admission = JobAdmission(max_pending=0, max_running=1)
    monkeypatch.setattr(ingest_route, "is_shutting_down", lambda: True)
    assert not ingest_route._schedule_background_task(  # noqa: SLF001
        delivery_id=None,
        payload={"ticket_id": 123},
        settings=make_settings(str(tmp_path)),
        admission=admission,
    )

    monkeypatch.setattr(ingest_route, "is_shutting_down", lambda: False)
    assert admission.try_reserve()
    assert not ingest_route._schedule_background_task(  # noqa: SLF001
        delivery_id=None,
        payload={"ticket_id": 123},
        settings=make_settings(str(tmp_path)),
        admission=admission,
    )
