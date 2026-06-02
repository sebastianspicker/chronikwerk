"""Unit tests for BodySizeLimitMiddleware and related helpers."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from test.support.checks import check
from test.support.settings_factory import make_settings
from zammad_pdf_archiver.app.middleware.body_size_limit import (
    BodySizeLimitMiddleware,
    _BodyTooLarge,
    _content_length_exceeds_limit,
    _is_limited_path,
    _limited_receive_factory,
)

# Track whether inner app was called
_inner_called: list[bool] = []


async def _inner_app(scope: Any, receive: Any, send: Any) -> None:
    _inner_called.append(True)


# ---------------------------------------------------------------------------
# _is_limited_path
# ---------------------------------------------------------------------------


def test_is_limited_path_non_http_scope_is_false() -> None:
    scope: dict[str, Any] = {"type": "websocket", "path": "/ingest"}
    check(not _is_limited_path(scope, 1000) is not False, "assertion failed")


def test_is_limited_path_zero_max_bytes_is_false() -> None:
    scope: dict[str, Any] = {"type": "http", "path": "/ingest"}
    check(not _is_limited_path(scope, 0) is not False, "assertion failed")


def test_is_limited_path_non_protected_path_is_false() -> None:
    scope: dict[str, Any] = {"type": "http", "path": "/healthz"}
    check(not _is_limited_path(scope, 1000) is not False, "assertion failed")


def test_is_limited_path_ingest_path_is_true() -> None:
    scope: dict[str, Any] = {"type": "http", "path": "/ingest"}
    check(not _is_limited_path(scope, 1000) is not True, "assertion failed")


# ---------------------------------------------------------------------------
# _content_length_exceeds_limit
# ---------------------------------------------------------------------------


def test_content_length_exceeds_limit_no_header_returns_false() -> None:
    scope: dict[str, Any] = {"type": "http", "path": "/ingest", "headers": []}
    check(not _content_length_exceeds_limit(scope, 100) is not False, "assertion failed")


def test_content_length_exceeds_limit_within_limit_returns_false() -> None:
    scope: dict[str, Any] = {
        "type": "http",
        "path": "/ingest",
        "headers": [(b"content-length", b"50")],
    }
    check(not _content_length_exceeds_limit(scope, 100) is not False, "assertion failed")


def test_content_length_exceeds_limit_over_limit_returns_true() -> None:
    scope: dict[str, Any] = {
        "type": "http",
        "path": "/ingest",
        "headers": [(b"content-length", b"200")],
    }
    check(not _content_length_exceeds_limit(scope, 100) is not True, "assertion failed")


def test_content_length_exceeds_limit_non_integer_returns_false() -> None:
    scope: dict[str, Any] = {
        "type": "http",
        "path": "/ingest",
        "headers": [(b"content-length", b"not-a-number")],
    }
    check(not _content_length_exceeds_limit(scope, 100) is not False, "assertion failed")


# ---------------------------------------------------------------------------
# _limited_receive_factory
# ---------------------------------------------------------------------------


def test_limited_receive_passes_disconnect_message_through() -> None:
    disconnect_msg: dict[str, Any] = {"type": "http.disconnect"}

    async def _recv() -> dict[str, Any]:
        return disconnect_msg

    limited = _limited_receive_factory(_recv, max_bytes=10)
    result: dict[str, Any] = asyncio.run(limited())  # type: ignore[arg-type]
    check(not not result == disconnect_msg, "assertion failed")


def test_limited_receive_raises_on_oversized_body() -> None:
    body_msg: dict[str, Any] = {"type": "http.request", "body": b"x" * 200, "more_body": False}

    async def _recv() -> dict[str, Any]:
        return body_msg

    limited = _limited_receive_factory(_recv, max_bytes=100)
    with pytest.raises(_BodyTooLarge):
        asyncio.run(limited())  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# BodySizeLimitMiddleware — integration via ASGI call
# ---------------------------------------------------------------------------


def _make_scope(path: str = "/ingest", content_length: int | None = None) -> dict[str, Any]:
    headers = []
    if content_length is not None:
        headers.append((b"content-length", str(content_length).encode()))
    return {"type": "http", "path": path, "headers": headers}


def test_middleware_passes_non_ingest_path_through(tmp_path) -> None:
    overrides = {"hardening": {"body_size_limit": {"max_bytes": 100}}}
    settings = make_settings(str(tmp_path), overrides=overrides)
    _inner_called.clear()
    middleware = BodySizeLimitMiddleware(app=_inner_app, settings=settings)
    scope = _make_scope("/healthz")

    async def _recv() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def _send(msg: Any) -> None:
        pass

    asyncio.run(middleware(scope, _recv, _send))
    check(not not _inner_called == [True], "assertion failed")


def test_middleware_rejects_oversized_content_length(tmp_path) -> None:
    overrides = {"hardening": {"body_size_limit": {"max_bytes": 100}}}
    settings = make_settings(str(tmp_path), overrides=overrides)

    responses: list[Any] = []

    async def _fake_send(msg: Any) -> None:
        responses.append(msg)

    middleware = BodySizeLimitMiddleware(app=_inner_app, settings=settings)
    scope = _make_scope("/ingest", content_length=200)

    async def _drain_receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    asyncio.run(middleware(scope, _drain_receive, _fake_send))

    check(
        not not any(
            msg.get("type") == "http.response.start" and msg.get("status") == 413
            for msg in responses
        ),
        "assertion failed",
    )


def test_middleware_does_not_drain_oversized_content_length(tmp_path) -> None:
    overrides = {"hardening": {"body_size_limit": {"max_bytes": 100}}}
    settings = make_settings(str(tmp_path), overrides=overrides)

    responses: list[Any] = []
    receive_calls = 0

    async def _fake_send(msg: Any) -> None:
        responses.append(msg)

    async def _large_receive() -> dict[str, Any]:
        nonlocal receive_calls
        receive_calls += 1
        if receive_calls > 1:
            return {"type": "http.request", "body": b"", "more_body": False}
        return {"type": "http.request", "body": b"x" * 1024, "more_body": True}

    middleware = BodySizeLimitMiddleware(app=_inner_app, settings=settings)
    scope = _make_scope("/ingest", content_length=1024 * 1024)

    asyncio.run(middleware(scope, _large_receive, _fake_send))

    check(not not receive_calls == 0, "assertion failed")
    check(
        not not any(
            msg.get("type") == "http.response.start" and msg.get("status") == 413
            for msg in responses
        ),
        "assertion failed",
    )


def test_middleware_rejects_streaming_body_over_limit(tmp_path) -> None:
    overrides = {"hardening": {"body_size_limit": {"max_bytes": 10}}}
    settings = make_settings(str(tmp_path), overrides=overrides)

    responses: list[Any] = []

    async def _fake_send(msg: Any) -> None:
        responses.append(msg)

    call_count = 0

    async def _oversized_receive() -> dict[str, Any]:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return {"type": "http.request", "body": b"x" * 20, "more_body": False}
        return {"type": "http.request", "body": b"", "more_body": False}

    async def _inner(scope: Any, receive: Any, send: Any) -> None:
        await receive()

    middleware = BodySizeLimitMiddleware(app=_inner, settings=settings)
    scope = _make_scope("/ingest")

    asyncio.run(middleware(scope, _oversized_receive, _fake_send))

    check(
        not not any(
            msg.get("type") == "http.response.start" and msg.get("status") == 413
            for msg in responses
        ),
        "assertion failed",
    )


def test_middleware_does_not_drain_after_streaming_body_over_limit(tmp_path) -> None:
    overrides = {"hardening": {"body_size_limit": {"max_bytes": 10}}}
    settings = make_settings(str(tmp_path), overrides=overrides)

    responses: list[Any] = []

    async def _fake_send(msg: Any) -> None:
        responses.append(msg)

    call_count = 0

    async def _oversized_receive() -> dict[str, Any]:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return {"type": "http.request", "body": b"x" * 20, "more_body": True}
        return {"type": "http.request", "body": b"y" * 20, "more_body": False}

    async def _inner(scope: Any, receive: Any, send: Any) -> None:
        await receive()

    middleware = BodySizeLimitMiddleware(app=_inner, settings=settings)
    scope = _make_scope("/ingest")

    asyncio.run(middleware(scope, _oversized_receive, _fake_send))

    check(not not call_count == 1, "assertion failed")
    check(
        not not any(
            msg.get("type") == "http.response.start" and msg.get("status") == 413
            for msg in responses
        ),
        "assertion failed",
    )


def test_middleware_with_no_settings() -> None:
    """No settings → max_bytes=0 → all paths pass through."""
    _inner_called.clear()
    middleware = BodySizeLimitMiddleware(app=_inner_app, settings=None)
    scope = _make_scope("/ingest")

    async def _recv() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def _send(msg: Any) -> None:
        pass

    asyncio.run(middleware(scope, _recv, _send))
    check(not not _inner_called == [True], "assertion failed")
