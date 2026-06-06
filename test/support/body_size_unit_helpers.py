"""ASGI harness helpers for body-size middleware unit tests."""

from __future__ import annotations

from typing import Any

from test.support.checks import check
from test.support.settings_factory import make_settings
from zammad_pdf_archiver.app.middleware.body_size_limit import BodySizeLimitMiddleware

inner_called: list[bool] = []


async def inner_app(scope: Any, receive: Any, send: Any) -> None:
    inner_called.append(True)


def make_scope(path: str = "/ingest", content_length: int | None = None) -> dict[str, Any]:
    headers = []
    if content_length is not None:
        headers.append((b"content-length", str(content_length).encode()))
    return {"type": "http", "path": path, "headers": headers}


def middleware(tmp_path, *, max_bytes: int, app: Any = inner_app) -> BodySizeLimitMiddleware:
    settings = make_settings(
        str(tmp_path),
        overrides={"hardening": {"body_size_limit": {"max_bytes": max_bytes}}},
    )
    return BodySizeLimitMiddleware(app=app, settings=settings)


def capturing_send() -> tuple[list[Any], Any]:
    responses: list[Any] = []

    async def _fake_send(msg: Any) -> None:
        responses.append(msg)

    return responses, _fake_send


def assert_413_response(responses: list[Any]) -> None:
    check(
        not not any(
            msg.get("type") == "http.response.start" and msg.get("status") == 413
            for msg in responses
        ),
        "assertion failed",
    )


async def empty_receive() -> dict[str, Any]:
    return {"type": "http.request", "body": b"", "more_body": False}


async def noop_send(msg: Any) -> None:
    pass


async def receive_once_inner(scope: Any, receive: Any, send: Any) -> None:
    await receive()


def counted_receive(
    first: dict[str, Any],
    later: dict[str, Any] | None = None,
) -> tuple[dict[str, int], Any]:
    counter = {"count": 0}
    fallback = empty_request() if later is None else later

    async def _receive() -> dict[str, Any]:
        counter["count"] += 1
        return first if counter["count"] == 1 else fallback

    return counter, _receive


def empty_request() -> dict[str, Any]:
    return {"type": "http.request", "body": b"", "more_body": False}
