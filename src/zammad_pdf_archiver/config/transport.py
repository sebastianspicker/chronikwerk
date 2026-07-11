from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urlparse

from zammad_pdf_archiver.domain.errors import PermanentError


def _host_ip(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return None


def _validate_ip(host: str, *, allow_private_networks: bool, source: str) -> None:
    address = _host_ip(host)
    if address is not None and not allow_private_networks and not address.is_global:
        raise PermanentError(f"Outbound URL targets a non-global address: {source}")


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

    try:
        results = socket.getaddrinfo(
            host,
            parsed.port or (443 if scheme == "https" else 80),
            type=socket.SOCK_STREAM,
        )
    except OSError as exc:
        raise PermanentError("Outbound hostname could not be resolved") from exc
    addresses = {str(item[4][0]) for item in results if item[4]}
    if not addresses:
        raise PermanentError("Outbound hostname resolved to no addresses")
    for address in addresses:
        _validate_ip(address, allow_private_networks=False, source=url)


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
