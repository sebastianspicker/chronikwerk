"""Verify administration response hardening stays scoped to admin paths."""

from __future__ import annotations

import asyncio

from starlette.types import Message, Receive, Scope, Send

from chronikwerk.app.admin.security import AdminSecurityHeadersMiddleware


def test_non_admin_response_passes_through_without_admin_headers() -> None:
    messages: list[Message] = []

    async def downstream(_scope: Scope, _receive: Receive, send: Send) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send({"type": "http.response.body", "body": b"{}"})

    async def receive() -> Message:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: Message) -> None:
        messages.append(message)

    scope: Scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/healthz",
        "raw_path": b"/healthz",
        "query_string": b"",
        "root_path": "",
        "headers": [],
        "client": ("127.0.0.1", 50000),
        "server": ("localhost", 80),
    }

    middleware = AdminSecurityHeadersMiddleware(downstream)
    asyncio.run(middleware(scope, receive, send))

    assert messages[0]["headers"] == [(b"content-type", b"application/json")]
