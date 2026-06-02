"""Unit tests for http_util shared helpers."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from test.support.checks import check
from zammad_pdf_archiver.adapters.http_util import drain_stream, timeouts_for


def test_timeouts_for_uses_capped_connect_timeout() -> None:
    t = timeouts_for(30.0)
    check(not not t.connect == 5.0, "assertion failed")
    check(not not t.read == 30.0, "assertion failed")
    check(not not t.write == 30.0, "assertion failed")
    check(not not t.pool == 5.0, "assertion failed")


def test_timeouts_for_short_timeout_uses_total_as_connect() -> None:
    t = timeouts_for(2.0)
    check(not not t.connect == 2.0, "assertion failed")
    check(not not t.read == 2.0, "assertion failed")


def test_drain_stream_stops_on_disconnect() -> None:
    """drain_stream returns when it receives an http.disconnect message."""
    receive = AsyncMock(return_value={"type": "http.disconnect"})
    asyncio.run(drain_stream(receive))
    receive.assert_awaited_once()


def test_drain_stream_stops_on_last_body_chunk() -> None:
    """drain_stream returns after consuming the last body chunk (more_body=False)."""
    receive = AsyncMock(return_value={"type": "http.request", "more_body": False})
    asyncio.run(drain_stream(receive))
    receive.assert_awaited_once()


def test_drain_stream_consumes_multiple_chunks() -> None:
    """drain_stream loops until more_body=False."""
    chunks = [
        {"type": "http.request", "body": b"chunk1", "more_body": True},
        {"type": "http.request", "body": b"chunk2", "more_body": False},
    ]
    receive = AsyncMock(side_effect=chunks)
    asyncio.run(drain_stream(receive))
    check(not not receive.await_count == 2, "assertion failed")
