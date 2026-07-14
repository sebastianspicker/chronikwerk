"""Unit tests for BodySizeLimitMiddleware and related helpers."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

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
    assert _is_limited_path(scope, 1000) is False


def test_is_limited_path_zero_max_bytes_is_false() -> None:
    scope: dict[str, Any] = {"type": "http", "path": "/ingest"}
    assert _is_limited_path(scope, 0) is False


def test_is_limited_path_non_protected_path_is_false() -> None:
    scope: dict[str, Any] = {"type": "http", "path": "/healthz"}
    assert _is_limited_path(scope, 1000) is False


def test_is_limited_path_ingest_path_is_true() -> None:
    scope: dict[str, Any] = {"type": "http", "path": "/ingest"}
    assert _is_limited_path(scope, 1000) is True


# ---------------------------------------------------------------------------
# _content_length_exceeds_limit
# ---------------------------------------------------------------------------


def test_content_length_exceeds_limit_no_header_returns_false() -> None:
    scope: dict[str, Any] = {"type": "http", "path": "/ingest", "headers": []}
    assert _content_length_exceeds_limit(scope, 100) is False


def test_content_length_exceeds_limit_within_limit_returns_false() -> None:
    scope: dict[str, Any] = {
        "type": "http",
        "path": "/ingest",
        "headers": [(b"content-length", b"50")],
    }
    assert _content_length_exceeds_limit(scope, 100) is False


def test_content_length_exceeds_limit_over_limit_returns_true() -> None:
    scope: dict[str, Any] = {
        "type": "http",
        "path": "/ingest",
        "headers": [(b"content-length", b"200")],
    }
    assert _content_length_exceeds_limit(scope, 100) is True


def test_content_length_exceeds_limit_non_integer_returns_false() -> None:
    scope: dict[str, Any] = {
        "type": "http",
        "path": "/ingest",
        "headers": [(b"content-length", b"not-a-number")],
    }
    assert _content_length_exceeds_limit(scope, 100) is False


# ---------------------------------------------------------------------------
# _limited_receive_factory
# ---------------------------------------------------------------------------


def test_limited_receive_passes_disconnect_message_through() -> None:
    disconnect_msg: dict[str, Any] = {"type": "http.disconnect"}

    async def _recv() -> dict[str, Any]:
        return disconnect_msg

    limited = _limited_receive_factory(_recv, max_bytes=10)
    result: dict[str, Any] = asyncio.run(limited())  # type: ignore[arg-type]
    assert result == disconnect_msg


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
    return {"type": "http", "method": "POST", "path": path, "headers": headers}


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
    assert _inner_called == [True]


def test_middleware_rejects_oversized_content_length(tmp_path) -> None:
    overrides = {"hardening": {"body_size_limit": {"max_bytes": 100}}}
    settings = make_settings(str(tmp_path), overrides=overrides)

    responses: list[Any] = []

    async def _fake_send(msg: Any) -> None:
        responses.append(msg)

    middleware = BodySizeLimitMiddleware(app=_inner_app, settings=settings)
    scope = _make_scope("/ingest", content_length=200)

    async def _unread_receive() -> dict[str, Any]:
        raise AssertionError("known oversized bodies must not be drained")

    asyncio.run(middleware(scope, _unread_receive, _fake_send))

    assert any(
        msg.get("type") == "http.response.start" and msg.get("status") == 413
        for msg in responses
    )
    response_start = next(msg for msg in responses if msg.get("type") == "http.response.start")
    assert (b"connection", b"close") in response_start["headers"]


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

    assert any(
        msg.get("type") == "http.response.start" and msg.get("status") == 413
        for msg in responses
    )


def test_middleware_times_out_a_stalled_stream(tmp_path) -> None:
    settings = make_settings(
        str(tmp_path),
        overrides={
            "hardening": {
                "body_size_limit": {"max_bytes": 100, "timeout_seconds": 0.01}
            }
        },
    )
    responses: list[Any] = []
    stalled = asyncio.Event()

    async def _fake_send(message: Any) -> None:
        responses.append(message)

    async def _never_finishes() -> dict[str, Any]:
        await stalled.wait()
        raise AssertionError("stalled receive unexpectedly resumed")

    async def _inner(_scope: Any, receive: Any, _send: Any) -> None:
        await receive()

    middleware = BodySizeLimitMiddleware(app=_inner, settings=settings)
    asyncio.run(middleware(_make_scope("/ingest"), _never_finishes, _fake_send))

    response_start = next(msg for msg in responses if msg.get("type") == "http.response.start")
    assert response_start["status"] == 408
    assert (b"connection", b"close") in response_start["headers"]


@pytest.mark.parametrize("path", ["/admin/login", "/admin/api/v1/session"])
def test_middleware_caps_unauthenticated_admin_login_bodies(tmp_path, path: str) -> None:
    settings = make_settings(
        str(tmp_path),
        overrides={
            "admin": {
                "enabled": True,
                "access_token": "admin-access-token-that-is-at-least-32-characters",
                "state_dir": str(tmp_path / "admin-state"),
            },
            "hardening": {"body_size_limit": {"max_bytes": 0}},
        },
    )
    responses: list[Any] = []

    async def _fake_send(message: Any) -> None:
        responses.append(message)

    async def _drain_receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    middleware = BodySizeLimitMiddleware(app=_inner_app, settings=settings)
    scope = _make_scope(path, content_length=(16 * 1024) + 1)

    asyncio.run(middleware(scope, _drain_receive, _fake_send))

    assert any(
        message.get("type") == "http.response.start" and message.get("status") == 413
        for message in responses
    )


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/admin/configuration/validate"),
        ("POST", "/admin/api/v1/config/validate"),
        ("PUT", "/admin/api/v1/config/staged"),
        ("DELETE", "/admin/api/v1/session"),
    ],
)
def test_middleware_caps_all_admin_mutation_bodies(
    tmp_path,
    method: str,
    path: str,
) -> None:
    settings = make_settings(
        str(tmp_path),
        overrides={
            "admin": {
                "enabled": True,
                "access_token": "admin-access-token-that-is-at-least-32-characters",
                "state_dir": str(tmp_path / "admin-state"),
            }
        },
    )
    responses: list[Any] = []

    async def _unread_receive() -> dict[str, Any]:
        raise AssertionError("known oversized admin bodies must not be read")

    async def _send(message: Any) -> None:
        responses.append(message)

    middleware = BodySizeLimitMiddleware(_inner_app, settings=settings)
    scope = _make_scope(path, content_length=(256 * 1024) + 1)
    scope["method"] = method
    asyncio.run(middleware(scope, _unread_receive, _send))

    assert any(
        message.get("type") == "http.response.start" and message.get("status") == 413
        for message in responses
    )


def test_middleware_caps_chunked_unauthenticated_admin_body(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "zammad_pdf_archiver.app.middleware.body_size_limit._ADMIN_BODY_MAX_BYTES",
        10,
    )
    settings = make_settings(
        str(tmp_path),
        overrides={
            "admin": {
                "enabled": True,
                "access_token": "admin-access-token-that-is-at-least-32-characters",
                "state_dir": str(tmp_path / "admin-state"),
            }
        },
    )
    responses: list[Any] = []

    async def _inner(_scope: Any, receive: Any, _send: Any) -> None:
        await receive()

    async def _receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"x" * 11, "more_body": True}

    async def _send(message: Any) -> None:
        responses.append(message)

    middleware = BodySizeLimitMiddleware(_inner, settings=settings)
    asyncio.run(
        middleware(
            _make_scope("/admin/api/v1/config/validate"),
            _receive,
            _send,
        )
    )

    assert any(
        message.get("type") == "http.response.start" and message.get("status") == 413
        for message in responses
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
    assert _inner_called == [True]


def test_disabled_configured_limit_still_uses_absolute_safety_cap(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "zammad_pdf_archiver.app.middleware.body_size_limit._ABSOLUTE_INGEST_MAX_BYTES",
        10,
    )
    settings = make_settings(
        str(tmp_path),
        overrides={"hardening": {"body_size_limit": {"max_bytes": 0}}},
    )
    responses: list[Any] = []

    async def _inner(_scope: Any, receive: Any, _send: Any) -> None:
        await receive()

    async def _receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"x" * 11, "more_body": False}

    async def _send(message: Any) -> None:
        responses.append(message)

    middleware = BodySizeLimitMiddleware(_inner, settings=settings)
    asyncio.run(middleware(_make_scope("/ingest"), _receive, _send))

    assert any(
        message.get("type") == "http.response.start" and message.get("status") == 413
        for message in responses
    )
