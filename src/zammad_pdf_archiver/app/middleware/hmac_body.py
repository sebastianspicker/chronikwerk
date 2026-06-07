from __future__ import annotations

from collections.abc import Callable

from starlette.types import Message, Receive


async def read_body(
    receive: Receive, *, on_chunk: Callable[[bytes], None]
) -> tuple[list[bytes], bool]:
    """
    Read body and update MAC. Returns (chunks, disconnected).
    If disconnected is True, the caller treats the incomplete body as an auth failure.
    """
    chunks: list[bytes] = []
    while True:
        message = await receive()
        message_type = message.get("type")
        if message_type == "http.disconnect":
            return (chunks, True)
        if message_type != "http.request":
            continue

        body = message.get("body", b"")
        if body:
            chunks.append(body)
            on_chunk(body)

        if not message.get("more_body", False):
            return (chunks, False)


def replay_receive(chunks: list[bytes]) -> Receive:
    idx = 0

    async def receive() -> Message:
        """Replay buffered body chunks as ASGI http.request messages."""
        nonlocal idx
        if idx >= len(chunks):
            return {"type": "http.request", "body": b"", "more_body": False}

        body = chunks[idx]
        idx += 1
        return {"type": "http.request", "body": body, "more_body": idx < len(chunks)}

    return receive
