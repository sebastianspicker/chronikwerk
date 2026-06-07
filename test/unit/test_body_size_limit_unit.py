"""Unit tests for BodySizeLimitMiddleware and related helpers."""

from __future__ import annotations

import asyncio

from test.support.body_size_unit_helpers import (
    assert_413_response as _assert_413_response,
)
from test.support.body_size_unit_helpers import (
    capturing_send as _capturing_send,
)
from test.support.body_size_unit_helpers import (
    counted_receive as _counted_receive,
)
from test.support.body_size_unit_helpers import (
    empty_receive as _empty_receive,
)
from test.support.body_size_unit_helpers import (
    inner_app as _inner_app,
)
from test.support.body_size_unit_helpers import (
    inner_called as _inner_called,
)
from test.support.body_size_unit_helpers import (
    make_scope as _make_scope,
)
from test.support.body_size_unit_helpers import (
    middleware as _middleware,
)
from test.support.body_size_unit_helpers import (
    noop_send as _noop_send,
)
from test.support.body_size_unit_helpers import (
    receive_once_inner as _receive_once_inner,
)
from test.support.checks import check
from zammad_pdf_archiver.app.middleware.body_size_limit import BodySizeLimitMiddleware


def test_middleware_passes_non_ingest_path_through(tmp_path) -> None:
    _inner_called.clear()
    middleware = _middleware(tmp_path, max_bytes=100)
    scope = _make_scope("/healthz")

    asyncio.run(middleware(scope, _empty_receive, _noop_send))
    check(not not _inner_called == [True], "assertion failed")


def test_middleware_rejects_oversized_content_length(tmp_path) -> None:
    responses, send = _capturing_send()
    middleware = _middleware(tmp_path, max_bytes=100)
    scope = _make_scope("/ingest", content_length=200)

    asyncio.run(middleware(scope, _empty_receive, send))

    _assert_413_response(responses)


def test_middleware_does_not_drain_oversized_content_length(tmp_path) -> None:
    responses, send = _capturing_send()
    receive_counter, receive = _counted_receive(
        {"type": "http.request", "body": b"x" * 1024, "more_body": True}
    )

    middleware = _middleware(tmp_path, max_bytes=100)
    scope = _make_scope("/ingest", content_length=1024 * 1024)

    asyncio.run(middleware(scope, receive, send))

    check(not not receive_counter["count"] == 0, "assertion failed")
    _assert_413_response(responses)


def test_middleware_rejects_streaming_body_over_limit(tmp_path) -> None:
    responses, send = _capturing_send()
    _counter, receive = _counted_receive(
        {"type": "http.request", "body": b"x" * 20, "more_body": False}
    )

    middleware = _middleware(tmp_path, max_bytes=10, app=_receive_once_inner)
    scope = _make_scope("/ingest")

    asyncio.run(middleware(scope, receive, send))

    _assert_413_response(responses)


def test_middleware_does_not_drain_after_streaming_body_over_limit(tmp_path) -> None:
    responses, send = _capturing_send()
    receive_counter, receive = _counted_receive(
        {"type": "http.request", "body": b"x" * 20, "more_body": True},
        {"type": "http.request", "body": b"y" * 20, "more_body": False},
    )

    middleware = _middleware(tmp_path, max_bytes=10, app=_receive_once_inner)
    scope = _make_scope("/ingest")

    asyncio.run(middleware(scope, receive, send))

    check(not not receive_counter["count"] == 1, "assertion failed")
    _assert_413_response(responses)


def test_middleware_with_no_settings() -> None:
    """No settings → max_bytes=0 → all paths pass through."""
    _inner_called.clear()
    middleware = BodySizeLimitMiddleware(app=_inner_app, settings=None)
    scope = _make_scope("/ingest")

    asyncio.run(middleware(scope, _empty_receive, _noop_send))
    check(not not _inner_called == [True], "assertion failed")
