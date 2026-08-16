"""Verifies capped HTTP timeouts and pinned URLs preserve TLS host identity."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import httpx
import pytest

from chronikwerk.adapters.http_util import (
    ResponseBodyTooLargeError,
    pin_request_url,
    read_response_body_limited,
    timeouts_for,
)


def test_timeouts_for_uses_capped_connect_timeout() -> None:
    t = timeouts_for(30.0)
    assert t.connect == 5.0
    assert t.read == 30.0
    assert t.write == 30.0
    assert t.pool == 5.0


def test_timeouts_for_short_timeout_uses_total_as_connect() -> None:
    t = timeouts_for(2.0)
    assert t.connect == 2.0
    assert t.read == 2.0


def test_pin_request_url_preserves_host_and_tls_identity() -> None:
    url, headers, extensions = pin_request_url(
        httpx.URL("https://example.com:8443/api"),
        "93.184.216.34",
    )

    assert str(url) == "https://93.184.216.34:8443/api"
    assert headers == {"Host": "example.com:8443"}
    assert extensions == {"sni_hostname": "example.com"}


class _ChunkedBody(httpx.AsyncByteStream):
    """Yield deterministic chunks for bounded response-body tests."""

    def __init__(self, *chunks: bytes) -> None:
        self._chunks = chunks

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            yield chunk


def test_read_response_body_limited_streams_when_content_length_is_malformed() -> None:
    response = httpx.Response(
        200,
        headers={"Content-Length": "not-a-number"},
        stream=_ChunkedBody(b"hello", b" world"),
    )

    assert asyncio.run(read_response_body_limited(response, max_bytes=11)) == b"hello world"


def test_read_response_body_limited_rejects_chunk_overflow() -> None:
    response = httpx.Response(200, stream=_ChunkedBody(b"abc", b"de"))

    with pytest.raises(ResponseBodyTooLargeError, match="4-byte limit"):
        asyncio.run(read_response_body_limited(response, max_bytes=4))


def test_read_response_body_limited_allows_declared_exact_limit() -> None:
    response = httpx.Response(
        200,
        headers={"Content-Length": "5"},
        stream=_ChunkedBody(b"hello"),
    )

    assert asyncio.run(read_response_body_limited(response, max_bytes=5)) == b"hello"


@pytest.mark.parametrize("content_encoding", ["identity", "IDENTITY", " Identity ", ""])
def test_read_response_body_limited_allows_identity_encoding_variants(
    content_encoding: str,
) -> None:
    response = httpx.Response(
        200,
        headers={"Content-Encoding": content_encoding},
        stream=_ChunkedBody(b"body"),
    )

    assert asyncio.run(read_response_body_limited(response, max_bytes=4)) == b"body"
