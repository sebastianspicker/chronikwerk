from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import httpx
import pytest
import respx

import zammad_pdf_archiver.adapters.zammad.client as zammad_client_module
from zammad_pdf_archiver.adapters.zammad.client import (
    AsyncZammadClient,
    _parse_retry_after_seconds,
    _RetryPolicy,
    _ZammadRuntimeOptions,
)
from zammad_pdf_archiver.adapters.zammad.errors import (
    AuthError,
    ClientError,
    NotFoundError,
    RateLimitError,
    ServerError,
)
from zammad_pdf_archiver.domain.errors import PermanentError


async def _no_sleep(_: float) -> None:
    return None


def _test_runtime(
    *,
    retry_policy: _RetryPolicy | None = None,
    http_client: httpx.AsyncClient | None = None,
    allow_private_networks: bool = True,
) -> _ZammadRuntimeOptions:
    return _ZammadRuntimeOptions(
        retry_policy=retry_policy,
        sleep=_no_sleep,
        http_client=http_client,
        allow_private_networks=allow_private_networks,
    )


def test_get_ticket_success() -> None:
    async def run() -> None:
        async with AsyncZammadClient(
            base_url="https://zammad.example",
            api_token="test-token",
            _runtime=_test_runtime(),
        ) as client:
            ticket = await client.get_ticket(123)
            assert ticket.id == 123
            assert ticket.number == "20240123"
            assert ticket.owner is not None
            assert ticket.owner.login == "agent"

    with respx.mock:
        respx.get("https://zammad.example/api/v1/tickets/123").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": 123,
                    "number": "20240123",
                    "title": "Example ticket",
                    "owner": {"login": "agent"},
                    "updated_by": {"login": "agent"},
                    "customer": {"login": "customer"},
                    "created_at": "2024-01-01T00:00:00Z",
                    "updated_at": "2024-01-02T00:00:00Z",
                    "preferences": {"custom_fields": {"archive_path": "/mnt/archive"}},
                    "ignored_field": "extra",
                },
            )
        )
        asyncio.run(run())


def test_success_response_without_content_length_is_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _ChunkedBody(httpx.AsyncByteStream):
        chunks_yielded = 0

        async def __aiter__(self) -> AsyncIterator[bytes]:
            for _ in range(100):
                self.chunks_yielded += 1
                yield b"x" * (40 * 1024)

    body = _ChunkedBody()
    monkeypatch.setattr(zammad_client_module, "_MAX_RESPONSE_BODY_BYTES", 64 * 1024)

    async def run() -> None:
        async with AsyncZammadClient(
            base_url="https://zammad.example",
            api_token="test-token",
            _runtime=_test_runtime(),
        ) as client:
            with pytest.raises(ClientError, match="65536-byte limit"):
                await client.get_ticket(123)

    with respx.mock:
        respx.get("https://zammad.example/api/v1/tickets/123").mock(
            return_value=httpx.Response(
                200,
                headers={"Content-Type": "application/json"},
                stream=body,
            )
        )
        asyncio.run(run())

    assert body.chunks_yielded < 100


def test_success_response_declared_over_limit_is_rejected_before_body_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _UnreadBody(httpx.AsyncByteStream):
        was_read = False

        async def __aiter__(self) -> AsyncIterator[bytes]:
            self.was_read = True
            yield b"{}"

    body = _UnreadBody()
    monkeypatch.setattr(zammad_client_module, "_MAX_RESPONSE_BODY_BYTES", 8)

    async def run() -> None:
        async with AsyncZammadClient(
            base_url="https://zammad.example",
            api_token="test-token",
            _runtime=_test_runtime(),
        ) as client:
            with pytest.raises(ClientError, match="8-byte limit"):
                await client.get_ticket(123)

    with respx.mock:
        respx.get("https://zammad.example/api/v1/tickets/123").mock(
            return_value=httpx.Response(
                200,
                headers={"Content-Type": "application/json", "Content-Length": "9"},
                stream=body,
            )
        )
        asyncio.run(run())

    assert body.was_read is False


def test_compressed_success_response_is_rejected_before_body_read() -> None:
    class _UnreadBody(httpx.AsyncByteStream):
        was_read = False

        async def __aiter__(self) -> AsyncIterator[bytes]:
            self.was_read = True
            yield b"compressed"

    body = _UnreadBody()

    async def run() -> None:
        async with AsyncZammadClient(
            base_url="https://zammad.example",
            api_token="test-token",
            _runtime=_test_runtime(),
        ) as client:
            with pytest.raises(ClientError, match="compressed response"):
                await client.get_ticket(123)

    with respx.mock:
        route = respx.get("https://zammad.example/api/v1/tickets/123").mock(
            return_value=httpx.Response(
                200,
                headers={
                    "Content-Type": "application/json",
                    "Content-Encoding": "gzip",
                },
                stream=body,
            )
        )
        asyncio.run(run())

    assert route.calls[0].request.headers["Accept-Encoding"] == "identity"
    assert body.was_read is False


def test_transport_revalidates_dns_before_each_request(monkeypatch) -> None:
    async def run() -> None:
        async with AsyncZammadClient(
            base_url="https://zammad.example",
            api_token="test-token",
            _runtime=_test_runtime(allow_private_networks=False),
        ) as client:
            await client.get_ticket(123)
            with pytest.raises(PermanentError):
                await client.get_ticket(123)

    public = [(2, 1, 6, "", ("93.184.216.34", 443))]
    private = [(2, 1, 6, "", ("127.0.0.1", 443))]
    resolutions = iter([public, private])

    def resolve(*_args, **_kwargs):
        return next(resolutions)

    monkeypatch.setattr(
        "zammad_pdf_archiver.config.transport.socket.getaddrinfo",
        resolve,
    )
    with respx.mock:
        route = respx.get("https://93.184.216.34/api/v1/tickets/123").mock(
            return_value=httpx.Response(
                200,
                json={"id": 123, "number": "20240123", "preferences": {"custom_fields": {}}},
            )
        )
        asyncio.run(run())
        assert route.call_count == 1
        assert route.calls[0].request.headers["Host"] == "zammad.example"
        assert route.calls[0].request.extensions["sni_hostname"] == "zammad.example"


def test_list_tags_success() -> None:
    async def run() -> None:
        async with AsyncZammadClient(
            base_url="https://zammad.example",
            api_token="test-token",
            _runtime=_test_runtime(),
        ) as client:
            tags = await client.list_tags(123)
            assert tags.root == ["pdf:sign", "archived"]

    with respx.mock:
        respx.get(
            "https://zammad.example/api/v1/tags",
            params={"object": "Ticket", "o_id": "123"},
        ).mock(return_value=httpx.Response(200, json=["pdf:sign", "archived"]))
        asyncio.run(run())


def test_add_tag_success() -> None:
    async def run() -> None:
        async with AsyncZammadClient(
            base_url="https://zammad.example",
            api_token="test-token",
            _runtime=_test_runtime(),
        ) as client:
            await client.add_tag(123, "archived")

    with respx.mock:
        route = respx.post("https://zammad.example/api/v1/tags/add").mock(
            return_value=httpx.Response(200, json={"success": True})
        )
        asyncio.run(run())
        assert route.called


def test_remove_tag_success() -> None:
    async def run() -> None:
        async with AsyncZammadClient(
            base_url="https://zammad.example",
            api_token="test-token",
            _runtime=_test_runtime(),
        ) as client:
            await client.remove_tag(123, "archived")

    with respx.mock:
        route = respx.post("https://zammad.example/api/v1/tags/remove").mock(
            return_value=httpx.Response(200, json={"success": True})
        )
        asyncio.run(run())
        assert route.called


def test_create_internal_article_success() -> None:
    async def run() -> None:
        async with AsyncZammadClient(
            base_url="https://zammad.example",
            api_token="test-token",
            _runtime=_test_runtime(),
        ) as client:
            article = await client.create_internal_article(123, "Subject", "<p>Body</p>")
            assert article.id == 999
            assert article.internal is True
            assert article.subject == "Subject"
            assert article.body == "<p>Body</p>"

    with respx.mock:
        route = respx.post("https://zammad.example/api/v1/ticket_articles").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": 999,
                    "internal": True,
                    "subject": "Subject",
                    "body": "<p>Body</p>",
                    "content_type": "text/html",
                    "created_at": "2024-01-02T00:00:00Z",
                },
            )
        )
        asyncio.run(run())
        assert route.called


def test_create_internal_article_does_not_retry_transport_failures() -> None:
    async def run() -> None:
        async with AsyncZammadClient(
            base_url="https://zammad.example",
            api_token="test-token",
            _runtime=_test_runtime(
                retry_policy=_RetryPolicy(max_retries=3, backoff_base_seconds=0.0)
            ),
        ) as client:
            with pytest.raises(ServerError, match="after 1 attempts"):
                await client.create_internal_article(123, "Subject", "<p>Body</p>")

    with respx.mock:
        route = respx.post("https://zammad.example/api/v1/ticket_articles").mock(
            side_effect=httpx.ReadTimeout("timeout")
        )
        asyncio.run(run())
        assert route.call_count == 1


def test_create_internal_article_does_not_retry_server_errors() -> None:
    async def run() -> None:
        async with AsyncZammadClient(
            base_url="https://zammad.example",
            api_token="test-token",
            _runtime=_test_runtime(
                retry_policy=_RetryPolicy(max_retries=3, backoff_base_seconds=0.0)
            ),
        ) as client:
            with pytest.raises(ServerError, match="after 1 attempts"):
                await client.create_internal_article(123, "Subject", "<p>Body</p>")

    with respx.mock:
        route = respx.post("https://zammad.example/api/v1/ticket_articles").mock(
            return_value=httpx.Response(503, json={"error": "busy"})
        )
        asyncio.run(run())
        assert route.call_count == 1



def test_list_articles_success() -> None:
    async def run() -> None:
        async with AsyncZammadClient(
            base_url="https://zammad.example",
            api_token="test-token",
            _runtime=_test_runtime(),
        ) as client:
            articles = await client.list_articles(123)
            assert [a.id for a in articles] == [1, 2]
            assert articles[0].from_ == "agent@example.com"

    with respx.mock:
        respx.get("https://zammad.example/api/v1/ticket_articles/by_ticket/123").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {
                        "id": 1,
                        "created_at": "2024-01-01T00:00:00Z",
                        "internal": False,
                        "subject": "Hello",
                        "body": "Body",
                        "content_type": "text/plain",
                        "from": "agent@example.com",
                        "to": "support@example.com",
                        "attachments": [{"id": 10, "filename": "a.txt", "size": 123}],
                    },
                    {
                        "id": 2,
                        "created_at": "2024-01-02T00:00:00Z",
                        "internal": True,
                        "subject": "Note",
                        "body": "Internal",
                        "content_type": "text/plain",
                    },
                ],
            )
        )
        asyncio.run(run())


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


# ---------------------------------------------------------------------------
# New tests for missing coverage
# ---------------------------------------------------------------------------


def test_base_url_missing_scheme_raises_value_error() -> None:
    with pytest.raises(ValueError, match="scheme"):
        AsyncZammadClient(base_url="zammad.example", api_token="tok")


def test_aclose_without_owning_http_client_is_noop() -> None:
    """aclose is a no-op when an external http_client was supplied."""

    async def run() -> None:
        external = httpx.AsyncClient()
        client = AsyncZammadClient(
            base_url="https://zammad.example",
            api_token="tok",
            _runtime=_test_runtime(http_client=external),
        )
        # Should not raise or call aclose on the external client
        await client.aclose()
        await external.aclose()

    asyncio.run(run())


def test_list_tags_dict_response_with_tags_key() -> None:
    """list_tags handles Zammad versions that return {"tags": [...]} wrapper."""

    async def run() -> None:
        async with AsyncZammadClient(
            base_url="https://zammad.example",
            api_token="test-token",
            _runtime=_test_runtime(),
        ) as client:
            tags = await client.list_tags(123)
            assert tags.root == ["foo", "bar"]

    with respx.mock:
        respx.get(
            "https://zammad.example/api/v1/tags",
            params={"object": "Ticket", "o_id": "123"},
        ).mock(return_value=httpx.Response(200, json={"tags": ["foo", "bar"]}))
        asyncio.run(run())


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
            req = httpx.Request("GET", "https://zammad.example/api/v1/test")
            resp = httpx.Response(429, request=req)
            with pytest.raises(RateLimitError):
                client._raise_for_status(resp)  # noqa: SLF001

    asyncio.run(run())


def test_raise_for_status_500_direct() -> None:
    async def run() -> None:
        async with AsyncZammadClient(
            base_url="https://zammad.example",
            api_token="tok",
            _runtime=_test_runtime(),
        ) as client:
            req = httpx.Request("GET", "https://zammad.example/api/v1/test")
            resp = httpx.Response(500, request=req)
            with pytest.raises(ServerError):
                client._raise_for_status(resp)  # noqa: SLF001

    asyncio.run(run())


def test_parse_retry_after_none_returns_none() -> None:
    assert _parse_retry_after_seconds(None) is None


def test_parse_retry_after_invalid_string_returns_none() -> None:
    assert _parse_retry_after_seconds("not-a-number") is None


def test_parse_retry_after_negative_returns_none() -> None:
    assert _parse_retry_after_seconds("-5") is None
