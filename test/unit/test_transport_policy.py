from __future__ import annotations

import asyncio
import socket
from typing import Any

import httpx
import pytest

from zammad_pdf_archiver.config.transport import (
    PolicyEnforcingAsyncTransport,
    _PolicyNetworkBackend,
    validate_url_policy,
)
from zammad_pdf_archiver.domain.errors import PermanentError


@pytest.mark.parametrize(
    "url",
    ["https://127.0.0.1/api", "https://[::1]/api", "https://192.0.2.1/api"],
)
def test_non_global_ip_literals_are_rejected(url: str) -> None:
    with pytest.raises(PermanentError):
        validate_url_policy(url)


def test_private_network_override_allows_internal_literal() -> None:
    validate_url_policy(
        "http://127.0.0.1:8080/health",
        allow_insecure_http=True,
        allow_private_networks=True,
    )


def test_dns_resolution_is_fail_closed_and_checks_every_address(monkeypatch) -> None:
    def fake_getaddrinfo(*_args, **_kwargs):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("::1", 443, 0, 0)),
        ]

    monkeypatch.setattr("zammad_pdf_archiver.config.transport.socket.getaddrinfo", fake_getaddrinfo)
    with pytest.raises(PermanentError):
        validate_url_policy("https://example.test", resolve_dns=True)


def test_dns_resolution_error_is_rejected(monkeypatch) -> None:
    def fail(*_args, **_kwargs):
        raise OSError("resolver unavailable")

    monkeypatch.setattr("zammad_pdf_archiver.config.transport.socket.getaddrinfo", fail)
    with pytest.raises(PermanentError, match="could not be resolved"):
        validate_url_policy("https://example.test", resolve_dns=True)


class _RecordingBackend:
    def __init__(self) -> None:
        self.connections: list[tuple[str, int]] = []

    # The AnyIO backend protocol requires this six-parameter method shape.
    # pylint: disable=too-many-positional-arguments
    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Any = None,
    ) -> object:
        del timeout, local_address, socket_options
        self.connections.append((host, port))
        return object()

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Any = None,
    ) -> object:
        del path, timeout, socket_options
        return object()

    async def sleep(self, seconds: float) -> None:
        del seconds


@pytest.mark.parametrize(
    ("family", "resolved_address"),
    [
        (socket.AF_INET, "93.184.216.34"),
        (socket.AF_INET6, "2606:4700:4700::1111"),
    ],
)
def test_connection_backend_pins_validated_ipv4_and_ipv6(
    monkeypatch: pytest.MonkeyPatch,
    family: socket.AddressFamily,
    resolved_address: str,
) -> None:
    def resolve(*_args: Any, **_kwargs: Any) -> list[tuple[Any, ...]]:
        sockaddr: tuple[Any, ...]
        if family == socket.AF_INET6:
            sockaddr = (resolved_address, 443, 0, 0)
        else:
            sockaddr = (resolved_address, 443)
        return [(family, socket.SOCK_STREAM, 6, "", sockaddr)]

    monkeypatch.setattr("zammad_pdf_archiver.config.transport.socket.getaddrinfo", resolve)
    recorder = _RecordingBackend()
    backend = _PolicyNetworkBackend(recorder, allow_private_networks=False)

    asyncio.run(backend.connect_tcp("example.test", 443))

    assert recorder.connections == [(resolved_address, 443)]


def test_connection_backend_rejects_rebinding_before_connect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolutions = iter(
        [
            [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))],
            [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))],
        ]
    )

    def resolve(*_args: Any, **_kwargs: Any) -> list[tuple[Any, ...]]:
        return next(resolutions)

    monkeypatch.setattr("zammad_pdf_archiver.config.transport.socket.getaddrinfo", resolve)
    recorder = _RecordingBackend()
    backend = _PolicyNetworkBackend(recorder, allow_private_networks=False)

    asyncio.run(backend.connect_tcp("example.test", 443))
    with pytest.raises(PermanentError, match="non-global"):
        asyncio.run(backend.connect_tcp("example.test", 443))

    assert recorder.connections == [("93.184.216.34", 443)]


def test_transport_revalidates_dns_before_reusing_a_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolutions = iter(
        [
            [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))],
            [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))],
        ]
    )

    def resolve(*_args: Any, **_kwargs: Any) -> list[tuple[Any, ...]]:
        return next(resolutions)

    monkeypatch.setattr("zammad_pdf_archiver.config.transport.socket.getaddrinfo", resolve)
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"ok": True})

    transport = PolicyEnforcingAsyncTransport(_transport=httpx.MockTransport(handle))

    async def run() -> None:
        async with httpx.AsyncClient(transport=transport) as client:
            response = await client.get("https://example.test/first")
            assert response.status_code == 200
            with pytest.raises(PermanentError, match="non-global"):
                await client.get("https://example.test/second")

    asyncio.run(run())

    assert [str(request.url) for request in requests] == ["https://example.test/first"]
    assert requests[0].headers["Host"] == "example.test"


def test_transport_rejects_redirect_to_private_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "zammad_pdf_archiver.config.transport.socket.getaddrinfo",
        lambda *_args, **_kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))
        ],
    )
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(302, headers={"Location": "https://127.0.0.1/private"})

    transport = PolicyEnforcingAsyncTransport(_transport=httpx.MockTransport(handle))

    async def run() -> None:
        async with httpx.AsyncClient(transport=transport, follow_redirects=True) as client:
            with pytest.raises(PermanentError, match="non-global"):
                await client.get("https://example.test/start")

    asyncio.run(run())

    assert [str(request.url) for request in requests] == ["https://example.test/start"]
