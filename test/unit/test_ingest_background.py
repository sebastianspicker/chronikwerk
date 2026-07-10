from __future__ import annotations

# Route import follows app setup so the test patches the live background task.
# pylint: disable=import-outside-toplevel,wrong-import-order
# ruff: noqa: I001  # Pylint and Ruff classify the in-repository test package differently.

import asyncio

from starlette.requests import Request

from test.support.settings_factory import make_settings
from zammad_pdf_archiver.app.routes.ingest import IngestPayload
from zammad_pdf_archiver.app.server import create_app


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
