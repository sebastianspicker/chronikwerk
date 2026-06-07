from __future__ import annotations

from starlette.types import Scope


def client_key_from_scope(scope: Scope) -> str:
    client = scope.get("client")
    if isinstance(client, (list, tuple)) and client:
        host = client[0]
        if isinstance(host, str) and host:
            return host
    return "unknown"


def client_key_from_header(scope: Scope, header_name: str) -> str:
    """Extract rate-limit key from a request header (e.g. X-Forwarded-For).

    Security note: this header is trivially spoofable by clients unless a
    trusted reverse proxy (nginx, Caddy, cloud LB) strips/overwrites it
    before forwarding.  Only enable ``client_key_header`` when deployed
    behind such a proxy.  When the header is missing or empty we fall back
    to the ASGI-level client address so an attacker cannot bypass rate
    limiting by omitting the header.
    """
    headers = scope.get("headers") or []
    header_lower = header_name.lower().encode("utf-8")
    for name, value in headers:
        if name == header_lower and value:
            first = value.decode("utf-8", errors="replace").strip()
            if "," in first:
                first = first.split(",")[0].strip()
            if first:
                return first
            break
    # Security: fall back to connection-level client address when header is
    # absent or empty, so attackers cannot bypass rate limiting by omitting it.
    return client_key_from_scope(scope)


def client_key(scope: Scope, header_name: str | None) -> str:
    if header_name and header_name.strip():
        return client_key_from_header(scope, header_name.strip())
    return client_key_from_scope(scope)
