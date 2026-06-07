from __future__ import annotations

import httpx
import pytest
import respx

from test.support.checks import check
from test.support.credentials import fake_credential
from test.support.zammad_client_helpers import run_client_action
from zammad_pdf_archiver.adapters.zammad.client import (
    AsyncZammadClient,
    _parse_retry_after_seconds,
)
from zammad_pdf_archiver.adapters.zammad.errors import (
    AuthError,
    ClientError,
    NotFoundError,
    RateLimitError,
    ServerError,
)


@pytest.mark.parametrize(
    ("ticket_id", "status", "body", "expected_error", "api_token"),
    [
        (123, 401, {"error": "unauthorized"}, AuthError, "bad-token"),
        (404, 404, {"error": "not found"}, NotFoundError, "test-token"),
        (123, 400, {"error": "bad request"}, ClientError, "test-token"),
    ],
)
def test_get_ticket_response_error(
    ticket_id: int,
    status: int,
    body: dict[str, str],
    expected_error: type[AuthError | ClientError | NotFoundError],
    api_token: str,
) -> None:
    async def assert_response_error(client: AsyncZammadClient) -> None:
        with pytest.raises(expected_error):
            await client.get_ticket(ticket_id)

    with respx.mock:
        respx.get(f"https://zammad.example/api/v1/tickets/{ticket_id}").mock(
            return_value=httpx.Response(status, json=body)
        )
        run_client_action(assert_response_error, api_token=api_token)


def test_5xx_raises_server_error_after_retries() -> None:
    async def assert_server_error(client: AsyncZammadClient) -> None:
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
        run_client_action(assert_server_error)
        check(not not route.call_count == 4, "assertion failed")


def test_timeout_raises_server_error_after_retries() -> None:
    async def assert_timeout_error(client: AsyncZammadClient) -> None:
        with pytest.raises(ServerError, match="timeout"):
            await client.get_ticket(123)

    with respx.mock:
        respx.get("https://zammad.example/api/v1/tickets/123").mock(
            side_effect=[
                httpx.ReadTimeout("timeout"),
                httpx.ReadTimeout("timeout"),
                httpx.ReadTimeout("timeout"),
                httpx.ReadTimeout("timeout"),
            ]
        )
        run_client_action(assert_timeout_error)


def test_transport_error_raises_server_error_after_retries() -> None:
    async def assert_transport_error(client: AsyncZammadClient) -> None:
        with pytest.raises(ServerError, match="Network error"):
            await client.get_ticket(123)

    with respx.mock:
        respx.get("https://zammad.example/api/v1/tickets/123").mock(
            side_effect=[
                httpx.NetworkError("connection reset"),
                httpx.NetworkError("connection reset"),
                httpx.NetworkError("connection reset"),
                httpx.NetworkError("connection reset"),
            ]
        )
        run_client_action(assert_transport_error)


def test_rate_limit_exhausted_raises_rate_limit_error() -> None:
    async def assert_rate_limit(client: AsyncZammadClient) -> None:
        with pytest.raises(RateLimitError):
            await client.get_ticket(123)

    with respx.mock:
        respx.get("https://zammad.example/api/v1/tickets/123").mock(
            side_effect=[
                httpx.Response(429, headers={"Retry-After": "0"}),
                httpx.Response(429),
                httpx.Response(429),
                httpx.Response(429),
            ]
        )
        run_client_action(assert_rate_limit)


@pytest.mark.parametrize(
    ("status", "expected_error"),
    [(429, RateLimitError), (500, ServerError)],
)
def test_raise_for_status_direct(
    status: int, expected_error: type[RateLimitError | ServerError]
) -> None:
    async def assert_status(client: AsyncZammadClient) -> None:
        req = httpx.Request("GET", "https://zammad.example/api/v1/test")
        resp = httpx.Response(status, request=req)
        with pytest.raises(expected_error):
            client._raise_for_status(resp)  # noqa: SLF001

    run_client_action(assert_status, api_token=fake_credential("tok"))


def test_parse_retry_after_none_returns_none() -> None:
    check(not _parse_retry_after_seconds(None) is not None, "assertion failed")


def test_parse_retry_after_invalid_string_returns_none() -> None:
    check(not _parse_retry_after_seconds("not-a-number") is not None, "assertion failed")


def test_parse_retry_after_negative_returns_none() -> None:
    check(not _parse_retry_after_seconds("-5") is not None, "assertion failed")
