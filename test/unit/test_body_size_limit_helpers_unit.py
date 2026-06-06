"""Unit tests for body-size middleware helper functions."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from test.support.checks import check
from zammad_pdf_archiver.app.middleware.body_size_limit import (
    _BodyTooLarge,
    _content_length_exceeds_limit,
    _is_limited_path,
    _limited_receive_factory,
)


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
