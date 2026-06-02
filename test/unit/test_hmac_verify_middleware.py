from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from pathlib import Path
from typing import Any

import pytest
from starlette.types import Message, Receive, Scope, Send

from test.support.checks import check
from test.support.credentials import fake_credential
from test.support.settings_factory import make_settings
from zammad_pdf_archiver.app.middleware.hmac_verify import HmacVerifyMiddleware, _read_body


def _sign(body: bytes, secret: str, *, algorithm: str = "sha256") -> str:
    digestmod = hashlib.sha256 if algorithm == "sha256" else hashlib.sha1
    digest = hmac.new(secret.encode("utf-8"), body, digestmod).hexdigest()
    return f"{algorithm}={digest}"


def _scope(headers: list[tuple[bytes, bytes]]) -> Scope:
    return {
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/ingest",
        "raw_path": b"/ingest",
        "query_string": b"",
        "headers": headers,
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
    }


def _receive_from(chunks: list[bytes]) -> Receive:
    messages: list[Message] = [
        {"type": "http.request", "body": chunk, "more_body": index < len(chunks) - 1}
        for index, chunk in enumerate(chunks)
    ] or [{"type": "http.request", "body": b"", "more_body": False}]

    async def receive() -> Message:
        if messages:
            return messages.pop(0)
        return {"type": "http.request", "body": b"", "more_body": False}

    return receive


def _status(messages: list[Message]) -> int:
    for message in messages:
        if message.get("type") == "http.response.start":
            return int(message["status"])
    raise AssertionError("response start message was not sent")


def _json_body(messages: list[Message]) -> dict[str, Any]:
    body = b"".join(
        message.get("body", b"")
        for message in messages
        if message.get("type") == "http.response.body"
    )
    return json.loads(body)


async def _invoke_middleware(
    tmp_path: Path,
    *,
    chunks: list[bytes],
    headers: list[tuple[bytes, bytes]],
) -> tuple[list[Message], list[bytes]]:
    downstream_chunks: list[bytes] = []

    async def downstream(_scope: Scope, receive: Receive, send: Send) -> None:
        while True:
            message = await receive()
            if message.get("type") != "http.request":
                continue
            downstream_chunks.append(message.get("body", b""))
            if not message.get("more_body", False):
                break
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    settings = make_settings(
        str(tmp_path),
        secret=fake_credential("unit-secret"),
        allow_unsigned=False,
        allow_unsigned_when_no_secret=False,
        require_delivery_id=False,
    )
    middleware = HmacVerifyMiddleware(downstream, settings=settings)
    sent: list[Message] = []

    async def send(message: Message) -> None:
        sent.append(message)

    await middleware(_scope(headers), _receive_from(chunks), send)
    return sent, downstream_chunks


def test_read_body_returns_when_client_disconnects() -> None:
    async def receive() -> dict[str, object]:
        # Yield control so asyncio.wait_for can cancel reliably if this regresses.
        await asyncio.sleep(0)
        return {"type": "http.disconnect"}

    chunks, disconnected = asyncio.run(
        asyncio.wait_for(_read_body(receive, on_chunk=lambda _chunk: None), timeout=0.1)
    )
    check(not not chunks == [], "assertion failed")
    check(not disconnected is not True, "assertion failed")


def test_matching_hmac_passes_request_and_replays_original_body(tmp_path) -> None:
    chunks = [b'{"ticket":', b'{"id":123}', b"}\x00\xff"]
    body = b"".join(chunks)

    sent, downstream_chunks = asyncio.run(
        _invoke_middleware(
            tmp_path,
            chunks=chunks,
            headers=[(b"x-hub-signature", _sign(body, "unit-secret").encode("ascii"))],
        )
    )

    check(not not _status(sent) == 204, "assertion failed")
    check(not not downstream_chunks == chunks, "assertion failed")


def test_wrong_hmac_rejects_without_calling_downstream(tmp_path) -> None:
    body = b'{"ticket":{"id":123}}'
    sent, downstream_chunks = asyncio.run(
        _invoke_middleware(
            tmp_path,
            chunks=[body],
            headers=[(b"x-hub-signature", _sign(body + b" ", "unit-secret").encode("ascii"))],
        )
    )

    check(not not _status(sent) == 403, "assertion failed")
    check(
        not not _json_body(sent) == {"detail": "forbidden", "code": "forbidden"}, "assertion failed"
    )
    check(not not downstream_chunks == [], "assertion failed")


def test_wrong_hmac_uses_compare_digest_for_same_length_digest(tmp_path, monkeypatch) -> None:
    body = b'{"ticket":{"id":123}}'
    compare_calls = 0
    real_compare_digest = hmac.compare_digest

    def compare_digest(left: bytes, right: bytes) -> bool:
        nonlocal compare_calls
        compare_calls += 1
        return real_compare_digest(left, right)

    monkeypatch.setattr(
        "zammad_pdf_archiver.app.middleware.hmac_verify.hmac.compare_digest",
        compare_digest,
    )

    sent, downstream_chunks = asyncio.run(
        _invoke_middleware(
            tmp_path,
            chunks=[body],
            headers=[(b"x-hub-signature", _sign(body + b" ", "unit-secret").encode("ascii"))],
        )
    )

    check(not not _status(sent) == 403, "assertion failed")
    check(not not compare_calls == 1, "assertion failed")
    check(not not downstream_chunks == [], "assertion failed")


def test_missing_hmac_rejects_with_forbidden_body(tmp_path) -> None:
    sent, downstream_chunks = asyncio.run(_invoke_middleware(tmp_path, chunks=[b"{}"], headers=[]))

    check(not not _status(sent) == 403, "assertion failed")
    check(
        not not _json_body(sent) == {"detail": "forbidden", "code": "forbidden"}, "assertion failed"
    )
    check(not not downstream_chunks == [], "assertion failed")


@pytest.mark.parametrize(
    "signature",
    [
        "sha256",
        "sha256=not-hex",
        f"sha256={'00' * 20}",
        "md5=0123456789abcdef0123456789abcdef",
    ],
)
def test_malformed_hmac_header_rejects_with_forbidden_body(tmp_path, signature: str) -> None:
    sent, downstream_chunks = asyncio.run(
        _invoke_middleware(
            tmp_path,
            chunks=[b"{}"],
            headers=[(b"x-hub-signature", signature.encode("ascii"))],
        )
    )

    check(not not _status(sent) == 403, "assertion failed")
    check(
        not not _json_body(sent) == {"detail": "forbidden", "code": "forbidden"}, "assertion failed"
    )
    check(not not downstream_chunks == [], "assertion failed")
