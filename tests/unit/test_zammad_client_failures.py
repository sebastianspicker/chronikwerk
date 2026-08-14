"""Verifies Zammad client error mapping, retry, and transport-failure behavior."""

from __future__ import annotations

import asyncio

import httpx
import pytest
import respx

from chronikwerk.adapters.zammad.client import (
    AsyncZammadClient,
    _parse_retry_after_seconds,
    _RetryPolicy,
)
from chronikwerk.adapters.zammad.errors import (
    AuthError,
    ClientError,
    NotFoundError,
    RateLimitError,
    ServerError,
)
from tests.support.zammad_client_helpers import (
    _test_runtime,
    assert_create_internal_article_is_not_retried,
)


def test_create_internal_article_does_not_retry_transport_failures() -> None:
    with respx.mock:
        route = respx.post("https://zammad.example/api/v1/ticket_articles").mock(
            side_effect=httpx.ReadTimeout("timeout")
        )
        assert_create_internal_article_is_not_retried()
        assert route.call_count == 1


def test_create_internal_article_does_not_retry_server_errors() -> None:
    with respx.mock:
        route = respx.post("https://zammad.example/api/v1/ticket_articles").mock(
            return_value=httpx.Response(503, json={"error": "busy"})
        )
        assert_create_internal_article_is_not_retried()
        assert route.call_count == 1


def test_401_raises_auth_error() -> None:
    async def run() -> None:
        async with AsyncZammadClient(
            base_url="https://zammad.example",
            api_token="bad-token",
            _runtime=_test_runtime(),
        ) as client:
            with pytest.raises(AuthError):
                await client.get_ticket(123)

    with respx.mock:
        respx.get("https://zammad.example/api/v1/tickets/123").mock(
            return_value=httpx.Response(401, json={"error": "unauthorized"})
        )
        asyncio.run(run())


def test_404_raises_not_found_error() -> None:
    async def run() -> None:
        async with AsyncZammadClient(
            base_url="https://zammad.example",
            api_token="test-token",
            _runtime=_test_runtime(),
        ) as client:
            with pytest.raises(NotFoundError):
                await client.get_ticket(404)

    with respx.mock:
        respx.get("https://zammad.example/api/v1/tickets/404").mock(
            return_value=httpx.Response(404, json={"error": "not found"})
        )
        asyncio.run(run())


def test_5xx_raises_server_error_after_retries() -> None:
    async def run() -> None:
        async with AsyncZammadClient(
            base_url="https://zammad.example",
            api_token="test-token",
            _runtime=_test_runtime(),
        ) as client:
            with pytest.raises(ServerError):
                await client.get_ticket(123)

    with respx.mock:
        route = respx.get("https://zammad.example/api/v1/tickets/123").mock(
            side_effect=[
                httpx.Response(500, json={"error": "boom"}),
                httpx.Response(502, json={"error": "boom"}),
                httpx.Response(503, json={"error": "boom"}),
                httpx.Response(500, json={"error": "boom"}),
            ]
        )
        asyncio.run(run())
        assert route.call_count == 4


def test_list_tags_invalid_format_raises_client_error() -> None:
    """list_tags raises ClientError when tags value cannot be parsed as list[str]."""

    async def run() -> None:
        async with AsyncZammadClient(
            base_url="https://zammad.example",
            api_token="test-token",
            _runtime=_test_runtime(),
        ) as client:
            with pytest.raises(ClientError, match="unexpected"):
                await client.list_tags(123)

    with respx.mock:
        respx.get(
            "https://zammad.example/api/v1/tags",
            params={"object": "Ticket", "o_id": "123"},
        ).mock(return_value=httpx.Response(200, json={"tags": {"not": "a-list"}}))
        asyncio.run(run())


def test_timeout_raises_server_error_after_retries() -> None:
    async def run() -> None:
        async with AsyncZammadClient(
            base_url="https://zammad.example",
            api_token="test-token",
            _runtime=_test_runtime(
                retry_policy=_RetryPolicy(max_retries=1, backoff_base_seconds=0.0)
            ),
        ) as client:
            with pytest.raises(ServerError, match="timeout"):
                await client.get_ticket(123)

    with respx.mock:
        respx.get("https://zammad.example/api/v1/tickets/123").mock(
            side_effect=[
                httpx.ReadTimeout("timeout"),
                httpx.ReadTimeout("timeout"),
            ]
        )
        asyncio.run(run())


def test_transport_error_raises_server_error_after_retries() -> None:
    async def run() -> None:
        async with AsyncZammadClient(
            base_url="https://zammad.example",
            api_token="test-token",
            _runtime=_test_runtime(
                retry_policy=_RetryPolicy(max_retries=1, backoff_base_seconds=0.0)
            ),
        ) as client:
            with pytest.raises(ServerError, match="Network error"):
                await client.get_ticket(123)

    with respx.mock:
        respx.get("https://zammad.example/api/v1/tickets/123").mock(
            side_effect=[
                httpx.NetworkError("connection reset"),
                httpx.NetworkError("connection reset"),
            ]
        )
        asyncio.run(run())


def test_rate_limit_exhausted_raises_rate_limit_error() -> None:
    async def run() -> None:
        async with AsyncZammadClient(
            base_url="https://zammad.example",
            api_token="test-token",
            _runtime=_test_runtime(
                retry_policy=_RetryPolicy(max_retries=1, backoff_base_seconds=0.0)
            ),
        ) as client:
            with pytest.raises(RateLimitError):
                await client.get_ticket(123)

    with respx.mock:
        respx.get("https://zammad.example/api/v1/tickets/123").mock(
            side_effect=[
                httpx.Response(429, headers={"Retry-After": "0"}),
                httpx.Response(429),
            ]
        )
        asyncio.run(run())


def test_400_raises_client_error() -> None:
    async def run() -> None:
        async with AsyncZammadClient(
            base_url="https://zammad.example",
            api_token="test-token",
            _runtime=_test_runtime(),
        ) as client:
            with pytest.raises(ClientError):
                await client.get_ticket(123)

    with respx.mock:
        respx.get("https://zammad.example/api/v1/tickets/123").mock(
            return_value=httpx.Response(400, json={"error": "bad request"})
        )
        asyncio.run(run())


def test_raise_for_status_429_direct() -> None:
    async def run() -> None:
        async with AsyncZammadClient(
            base_url="https://zammad.example",
            api_token="tok",
            _runtime=_test_runtime(),
        ) as client:
            request = httpx.Request("GET", "https://zammad.example/api/v1/test")
            response = httpx.Response(429, request=request)
            with pytest.raises(RateLimitError):
                client._raise_for_status(response)  # noqa: SLF001

    asyncio.run(run())


def test_raise_for_status_500_direct() -> None:
    async def run() -> None:
        async with AsyncZammadClient(
            base_url="https://zammad.example",
            api_token="tok",
            _runtime=_test_runtime(),
        ) as client:
            request = httpx.Request("GET", "https://zammad.example/api/v1/test")
            response = httpx.Response(500, request=request)
            with pytest.raises(ServerError):
                client._raise_for_status(response)  # noqa: SLF001

    asyncio.run(run())


def test_parse_retry_after_none_returns_none() -> None:
    assert _parse_retry_after_seconds(None) is None


def test_parse_retry_after_invalid_string_returns_none() -> None:
    assert _parse_retry_after_seconds("not-a-number") is None


def test_parse_retry_after_negative_returns_none() -> None:
    assert _parse_retry_after_seconds("-5") is None
