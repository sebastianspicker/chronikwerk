"""Verifies HMAC middleware handles disconnects and rejects unsigned bodies safely."""

from __future__ import annotations

import asyncio

import pytest
from starlette.types import Message, Receive, Scope, Send

from chronikwerk.app.middleware.hmac_verify import HmacVerifyMiddleware, _read_body
from chronikwerk.config.settings import Settings
from tests.support.settings_factory import make_settings


def _invoke_protected_request(
    *,
    settings: Settings | None,
    receive: Receive,
    headers: tuple[tuple[bytes, bytes], ...] = (),
    app_failure: str = "inner app must not run",
) -> Message:
    """Run one protected request and return its response-start message."""
    responses: list[Message] = []

    async def app(_scope: Scope, _receive: Receive, _send: Send) -> None:
        raise AssertionError(app_failure)

    async def send(message: Message) -> None:
        responses.append(message)

    middleware = HmacVerifyMiddleware(app, settings=settings)
    scope: Scope = {"type": "http", "method": "POST", "path": "/ingest", "headers": headers}
    asyncio.run(middleware(scope, receive, send))
    return next(message for message in responses if message.get("type") == "http.response.start")


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
    async def receive() -> Message:
        raise AssertionError("rejected body must not be drained")

    response_start = _invoke_protected_request(
        settings=make_settings(str(tmp_path), secret="test-secret"),
        receive=receive,
    )

    assert response_start["status"] == 403
    assert (b"connection", b"close") in response_start["headers"]


@pytest.mark.parametrize("secret", [None, "   "])
def test_absent_settings_or_blank_secret_rejects_as_service_misconfigured(tmp_path, secret) -> None:
    async def receive() -> Message:
        raise AssertionError("misconfigured requests must not be drained")

    settings = None if secret is None else make_settings(str(tmp_path), secret=secret)
    response_start = _invoke_protected_request(settings=settings, receive=receive)

    assert response_start["status"] == 503
    assert (b"connection", b"close") in response_start["headers"]


@pytest.mark.parametrize(
    "signature",
    ["sha256", "sha1=00", "sha256=not-hex", "sha256=00", "sha256=" + "00" * 31],
)
def test_malformed_invalid_or_wrong_length_signature_is_rejected_before_body_read(
    tmp_path, signature: str
) -> None:
    async def receive() -> Message:
        raise AssertionError("invalid signatures must not be drained")

    response_start = _invoke_protected_request(
        settings=make_settings(str(tmp_path), secret="test-secret"),
        receive=receive,
        headers=((b"x-hub-signature", signature.encode("ascii")),),
    )

    assert response_start["status"] == 403


def test_wrong_but_well_formed_signature_is_rejected_after_body_read(tmp_path) -> None:
    async def receive() -> Message:
        return {"type": "http.request", "body": b"signed-body", "more_body": False}

    response_start = _invoke_protected_request(
        settings=make_settings(str(tmp_path), secret="test-secret"),
        receive=receive,
        headers=((b"x-hub-signature", b"sha256=" + b"00" * 32),),
    )

    assert response_start["status"] == 403


def test_disconnect_after_a_syntactically_valid_signature_fails_closed(tmp_path) -> None:
    async def receive() -> Message:
        return {"type": "http.disconnect"}

    response_start = _invoke_protected_request(
        settings=make_settings(str(tmp_path), secret="test-secret"),
        receive=receive,
        headers=((b"x-hub-signature", b"sha256=" + b"00" * 32),),
        app_failure="inner app must not run after disconnect",
    )

    assert response_start["status"] == 403
