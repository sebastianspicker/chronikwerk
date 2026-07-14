from __future__ import annotations

import asyncio

from test.support.settings_factory import make_settings
from zammad_pdf_archiver.app.middleware.hmac_verify import HmacVerifyMiddleware, _read_body


def test_read_body_returns_when_client_disconnects() -> None:
    async def receive() -> dict[str, object]:
        # Yield control so asyncio.wait_for can cancel reliably if this regresses.
        await asyncio.sleep(0)
        return {"type": "http.disconnect"}

    chunks, disconnected = asyncio.run(
        asyncio.wait_for(_read_body(receive, on_chunk=lambda _chunk: None), timeout=0.1)
    )
    assert chunks == []
    assert disconnected is True


def test_missing_signature_rejects_without_draining_body(tmp_path) -> None:
    responses: list[dict[str, object]] = []

    async def app(*_args) -> None:
        raise AssertionError("inner app must not run")

    async def receive() -> dict[str, object]:
        raise AssertionError("rejected body must not be drained")

    async def send(message: dict[str, object]) -> None:
        responses.append(message)

    middleware = HmacVerifyMiddleware(
        app,
        settings=make_settings(str(tmp_path), secret="test-secret"),
    )
    scope = {"type": "http", "method": "POST", "path": "/ingest", "headers": []}
    asyncio.run(middleware(scope, receive, send))  # type: ignore[arg-type]

    response_start = next(msg for msg in responses if msg.get("type") == "http.response.start")
    assert response_start["status"] == 403
    assert (b"connection", b"close") in response_start["headers"]  # type: ignore[operator]
