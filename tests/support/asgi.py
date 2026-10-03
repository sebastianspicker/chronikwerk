"""Drive an ASGI application directly with scripted request messages."""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from starlette.types import ASGIApp, Message, Scope


@dataclass
class AsgiResult:
    """Collected response of one scripted ASGI exchange."""

    status: int = 0
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""

    def json(self) -> Any:
        """Decode the buffered response body as JSON."""
        return json.loads(self.body)


async def asgi_post(
    app: ASGIApp,
    path: str,
    messages: Iterable[Message],
    *,
    headers: dict[str, str] | None = None,
    client: tuple[str, int] = ("203.0.113.9", 4000),
) -> AsgiResult:
    """Send one POST whose body arrives as the given ASGI receive messages."""
    scripted = iter(messages)
    raw_headers = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    scope: Scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": raw_headers,
        "client": client,
        "server": ("testserver", 80),
    }
    result = AsgiResult()

    async def receive() -> Message:
        return next(scripted, {"type": "http.disconnect"})

    async def send(message: Message) -> None:
        if message["type"] == "http.response.start":
            result.status = message["status"]
            result.headers = {k.decode().lower(): v.decode() for k, v in message["headers"]}
        elif message["type"] == "http.response.body":
            result.body += message.get("body", b"")

    await app(scope, receive, send)
    return result


def body_chunks(*chunks: bytes) -> list[Message]:
    """Build http.request messages that deliver the chunks as one streamed body."""
    last = len(chunks) - 1
    return [
        {"type": "http.request", "body": chunk, "more_body": index < last}
        for index, chunk in enumerate(chunks)
    ]
