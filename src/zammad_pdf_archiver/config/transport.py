"""Project module."""
from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Iterable
from typing import Any, Protocol
from urllib.parse import urlparse

import httpx

from zammad_pdf_archiver.domain.errors import PermanentError

_IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
_SocketOption = tuple[int, int, int | bytes | bytearray] | tuple[int, int, None, int]


class _AsyncNetworkBackend(Protocol):
    # Signature must match httpcore's positional network-backend protocol.
    async def connect_tcp(  # pylint: disable=too-many-positional-arguments
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[_SocketOption] | None = None,
    ) -> Any: ...

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Iterable[_SocketOption] | None = None,
    ) -> Any: ...

    async def sleep(self, seconds: float) -> None: ...


def _host_ip(host: str) -> _IPAddress | None:
    try:
        return ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return None


def _validate_ip(host: str, *, allow_private_networks: bool, source: str) -> None:
    address = _host_ip(host)
    if address is not None and not allow_private_networks and not address.is_global:
        raise PermanentError(f"Outbound URL targets a non-global address: {source}")


def _resolve_host(host: str, port: int) -> tuple[_IPAddress, ...]:
    try:
        results = socket.getaddrinfo(
            host.rstrip("."),
            port,
            type=socket.SOCK_STREAM,
        )
    except OSError as exc:
        raise PermanentError("Outbound hostname could not be resolved") from exc

    addresses: list[_IPAddress] = []
    for result in results:
        if not result[4]:
            continue
        address = _host_ip(str(result[4][0]))
        if address is not None and address not in addresses:
            addresses.append(address)
    if not addresses:
        raise PermanentError("Outbound hostname resolved to no addresses")
    return tuple(addresses)


def _resolved_addresses(url: str) -> tuple[_IPAddress, ...]:
    parsed = urlparse(url)
    host = parsed.hostname
    if host is None:  # Defensive: validate_url_policy rejects this first.
        raise PermanentError("Outbound URL must include an https:// host")
    port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
    return _resolve_host(host, port)


def validate_url_policy(
    url: str,
    *,
    allow_insecure_http: bool = False,
    allow_private_networks: bool = False,
    resolve_dns: bool = False,
) -> None:
    """Validate URL transport and, optionally, every DNS-resolved address."""
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    if scheme not in {"https", "http"} or not parsed.hostname:
        raise PermanentError("Outbound URL must include an https:// host")
    if scheme == "http" and not allow_insecure_http:
        raise PermanentError("Plain HTTP upstream URL is not allowed")

    host = parsed.hostname.rstrip(".").lower()
    localhost_names = {
        "localhost",
        "localhost.localdomain",
        "localhost6",
        "localhost6.localdomain",
        "ip6-localhost",
    }
    if host in localhost_names or host.endswith(".localhost"):
        if not allow_private_networks:
            raise PermanentError("Localhost outbound URL is not allowed")
        return

    _validate_ip(host, allow_private_networks=allow_private_networks, source=url)
    if allow_private_networks or _host_ip(host) is not None or not resolve_dns:
        return

    for address in _resolved_addresses(url):
        _validate_ip(str(address), allow_private_networks=False, source=url)


async def validate_url_policy_async(
    url: str,
    *,
    allow_insecure_http: bool = False,
    allow_private_networks: bool = False,
) -> None:
    """Run DNS resolution off the event loop before an outbound request."""
    await asyncio.to_thread(
        validate_url_policy,
        url,
        allow_insecure_http=allow_insecure_http,
        allow_private_networks=allow_private_networks,
        resolve_dns=True,
    )


async def _validated_connection_address(
    url: str,
    *,
    allow_insecure_http: bool,
    allow_private_networks: bool,
) -> _IPAddress | None:
    validate_url_policy(
        url,
        allow_insecure_http=allow_insecure_http,
        allow_private_networks=allow_private_networks,
    )
    parsed = urlparse(url)
    host = parsed.hostname
    if host is None:  # Defensive: validate_url_policy rejects this first.
        raise PermanentError("Outbound URL must include an https:// host")

    literal = _host_ip(host)
    if literal is not None or allow_private_networks:
        return literal

    addresses = await asyncio.to_thread(_resolved_addresses, url)
    for address in addresses:
        _validate_ip(str(address), allow_private_networks=False, source=url)
    return addresses[0]


class _PolicyNetworkBackend:
    """Resolve and pin a validated address at the TCP connection boundary."""

    def __init__(
        self,
        backend: _AsyncNetworkBackend,
        *,
        allow_private_networks: bool,
    ) -> None:
        self._backend = backend
        self._allow_private_networks = allow_private_networks

    async def connect_tcp(  # pylint: disable=too-many-positional-arguments
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[_SocketOption] | None = None,
    ) -> Any:
        literal = _host_ip(host)
        if literal is not None:
            _validate_ip(
                str(literal),
                allow_private_networks=self._allow_private_networks,
                source=host,
            )
            target_host = str(literal)
        elif self._allow_private_networks:
            target_host = host
        else:
            addresses = await asyncio.to_thread(_resolve_host, host, port)
            for address in addresses:
                _validate_ip(str(address), allow_private_networks=False, source=host)
            target_host = str(addresses[0])

        return await self._backend.connect_tcp(
            target_host,
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Iterable[_SocketOption] | None = None,
    ) -> Any:
        return await self._backend.connect_unix_socket(
            path,
            timeout=timeout,
            socket_options=socket_options,
        )

    async def sleep(self, seconds: float) -> None:
        await self._backend.sleep(seconds)


class _ConnectionBoundaryAsyncHTTPTransport(  # pylint: disable=too-few-public-methods
    httpx.AsyncHTTPTransport
):
    def __init__(
        self,
        *,
        allow_private_networks: bool,
        verify: bool | str,
        trust_env: bool,
        limits: httpx.Limits,
    ) -> None:
        super().__init__(verify=verify, trust_env=trust_env, limits=limits)
        pool: Any = getattr(self, "_pool", None)
        backend = getattr(pool, "_network_backend", None)
        if backend is None:
            raise RuntimeError("httpx transport does not expose a network backend")
        pool._network_backend = _PolicyNetworkBackend(  # noqa: SLF001
            backend,
            allow_private_networks=allow_private_networks,
        )


class PolicyEnforcingAsyncTransport(httpx.AsyncBaseTransport):
    """Revalidate every request and pin new TCP connections to validated IPs."""

    def __init__(
        self,
        *,
        allow_insecure_http: bool = False,
        allow_private_networks: bool = False,
        verify: bool | str = True,
        trust_env: bool = False,
        limits: httpx.Limits | None = None,
        _transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """Configure URL policy and the lazily created connection pool."""
        self._allow_insecure_http = allow_insecure_http
        self._allow_private_networks = allow_private_networks
        self._verify = verify
        self._trust_env = trust_env
        self._limits = limits or httpx.Limits()
        self._transport = _transport

    def _connection_transport(self) -> httpx.AsyncBaseTransport:
        if self._transport is None:
            self._transport = _ConnectionBoundaryAsyncHTTPTransport(
                allow_private_networks=self._allow_private_networks,
                verify=self._verify,
                trust_env=self._trust_env,
                limits=self._limits,
            )
        return self._transport

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        """Validate DNS and pin the URL before the underlying transport connects."""
        await _validated_connection_address(
            str(request.url),
            allow_insecure_http=self._allow_insecure_http,
            allow_private_networks=self._allow_private_networks,
        )
        return await self._connection_transport().handle_async_request(request)

    async def aclose(self) -> None:
        """Close the underlying connection pool."""
        if self._transport is not None:
            await self._transport.aclose()
