"""Validate outbound HTTP transport settings and TLS trust material."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import ParseResult, urlparse

from chronikwerk.domain.errors import PermanentError, TransientError

_PERMANENT_DNS_ERRORS = frozenset(
    error
    for name in ("EAI_NONAME", "EAI_NODATA", "EAI_BADFLAGS", "EAI_FAMILY", "EAI_SERVICE")
    if isinstance(error := getattr(socket, name, None), int)
)


def _host_ip(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return None


def _validate_ip(host: str, *, allow_private_networks: bool) -> None:
    address = _host_ip(host)
    if address is not None and not allow_private_networks and not address.is_global:
        raise PermanentError("Outbound URL targets a non-global address")


def _validated_url_host(
    url: str,
    *,
    allow_insecure_http: bool,
    allow_private_networks: bool,
) -> tuple[ParseResult, str]:
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    if scheme not in {"https", "http"} or not parsed.hostname:
        raise PermanentError("Outbound URL must include an https:// host")
    if parsed.username is not None or parsed.password is not None:
        raise PermanentError("Outbound URL must not include credentials")
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
    if (host in localhost_names or host.endswith(".localhost")) and not allow_private_networks:
        raise PermanentError("Localhost outbound URL is not allowed")
    _validate_ip(host, allow_private_networks=allow_private_networks)
    return parsed, host


def _resolved_addresses(host: str, port: int) -> tuple[str, ...]:
    try:
        results = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        if exc.errno in _PERMANENT_DNS_ERRORS:
            raise PermanentError("Outbound hostname could not be resolved") from exc
        raise TransientError("Outbound DNS resolver is temporarily unavailable") from exc
    except OSError as exc:
        raise TransientError("Outbound DNS resolver is temporarily unavailable") from exc

    addresses = tuple(dict.fromkeys(str(item[4][0]) for item in results if item[4]))
    if not addresses:
        raise PermanentError("Outbound hostname resolved to no addresses")
    return addresses


def validate_url_policy(
    url: str,
    *,
    allow_insecure_http: bool = False,
    allow_private_networks: bool = False,
    resolve_dns: bool = False,
) -> tuple[str, ...]:
    """Validate URL transport and, optionally, every DNS-resolved address."""
    parsed, host = _validated_url_host(
        url,
        allow_insecure_http=allow_insecure_http,
        allow_private_networks=allow_private_networks,
    )
    literal_address = _host_ip(host)
    if literal_address is not None:
        return (str(literal_address),)
    if allow_private_networks or not resolve_dns:
        return ()

    addresses = _resolved_addresses(
        host,
        parsed.port or (443 if parsed.scheme.lower() == "https" else 80),
    )
    for address in addresses:
        _validate_ip(address, allow_private_networks=False)
    return addresses


async def validate_url_policy_async(
    url: str,
    *,
    allow_insecure_http: bool = False,
    allow_private_networks: bool = False,
    timeout_seconds: float = 5.0,
) -> str | None:
    """Validate and return one safe address that callers can pin for connection."""
    try:
        addresses = await asyncio.wait_for(
            asyncio.to_thread(
                validate_url_policy,
                url,
                allow_insecure_http=allow_insecure_http,
                allow_private_networks=allow_private_networks,
                resolve_dns=True,
            ),
            timeout=max(0.001, float(timeout_seconds)),
        )
    except TimeoutError as exc:
        raise TransientError("Outbound DNS resolution timed out") from exc
    return addresses[0] if addresses else None
