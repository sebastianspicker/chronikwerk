from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

from test.support.settings_factory import make_settings
from zammad_pdf_archiver.app.jobs import redis_queue


class AckDeleteMixin:
    async def xack(self: Any, stream: str, group: str, message_id: str) -> int:
        self.acked.append((stream, group, message_id))
        return 1

    async def xdel(self: Any, stream: str, message_id: str) -> int:
        self.deleted.append((stream, message_id))
        return 1


@dataclass
class FakeRedis(AckDeleteMixin):
    """Minimal fake Redis supporting xreadgroup, xadd, xack, xdel, xlen, xpending."""

    xadds: list[tuple[str, dict[str, str]]] = field(default_factory=list)
    acked: list[tuple[str, str, str]] = field(default_factory=list)
    deleted: list[tuple[str, str]] = field(default_factory=list)
    stream_lengths: dict[str, int] = field(default_factory=dict)
    pending_count: int = 0
    xreadgroup_responses: dict[str, Any] = field(default_factory=dict)
    groups_created: list[tuple[str, str]] = field(default_factory=list)

    async def xreadgroup(
        self,
        groupname: str,  # noqa: ARG002
        consumername: str,  # noqa: ARG002
        streams: dict[str, str],
        count: int = 10,  # noqa: ARG002
        block: int | None = None,  # noqa: ARG002
    ) -> Any:
        stream_id = next(iter(streams.values()))
        return self.xreadgroup_responses.get(stream_id)

    async def xadd(self, stream: str, fields: dict[str, str], **kwargs: Any) -> str:  # noqa: ARG002
        self.xadds.append((stream, fields))
        self.stream_lengths[stream] = self.stream_lengths.get(stream, 0) + 1
        return f"{len(self.xadds)}-0"

    async def xlen(self, stream: str) -> int:
        return self.stream_lengths.get(stream, 0)

    async def xpending(self, stream: str, group: str) -> dict[str, int]:  # noqa: ARG002
        return {"pending": self.pending_count}

    async def xgroup_create(
        self,
        stream: str,
        group: str,
        **kwargs: Any,  # noqa: ARG002
    ) -> None:
        self.groups_created.append((stream, group))

    async def aclose(self) -> None:
        pass


class WorkerRedis(AckDeleteMixin):
    def __init__(self) -> None:
        self.messages: list[tuple[str, dict[str, str]]] = []
        self.acked: list[tuple[str, str, str]] = []
        self.deleted: list[tuple[str, str]] = []
        self.groups_created: list[tuple[str, str, str, bool]] = []

    async def xadd(self, stream: str, fields: dict[str, str]) -> str:
        message_id = f"{len(self.messages) + 1}-0"
        self.messages.append((message_id, fields))
        return message_id

    async def xgroup_create(
        self,
        stream: str,
        group: str,
        **kwargs: Any,
    ) -> None:
        group_id = str(kwargs["id"])
        mkstream = bool(kwargs["mkstream"])
        self.groups_created.append((stream, group, group_id, mkstream))

    async def xpending_range(self, *args: Any) -> list[dict[str, str]]:  # noqa: ARG002
        await asyncio.sleep(0)
        return []

    async def xreadgroup(
        self,
        groupname: str,  # noqa: ARG002
        consumername: str,  # noqa: ARG002
        streams: dict[str, str],
        count: int = 10,
        block: int | None = None,  # noqa: ARG002
    ) -> list[tuple[str, list[tuple[str, dict[str, str]]]]]:
        stream, stream_id = next(iter(streams.items()))
        if stream_id != ">" or not self.messages:
            await asyncio.sleep(0)
            return []
        messages = self.messages[:count]
        self.messages = self.messages[count:]
        await asyncio.sleep(0)
        return [(stream, messages)]

    async def ping(self) -> None:
        return None


def make_redis_settings(tmp_path: Any, **extra: Any) -> Any:
    overrides: dict[str, Any] = {
        "workflow": {
            "execution_backend": "redis_queue",
            "redis_url": "redis://localhost/0",
        }
    }
    overrides["workflow"].update(extra)
    return make_settings(str(tmp_path), overrides=overrides)


def valid_raw_fields(ticket_id: int = 42, attempt: int = 0) -> dict[str, str]:
    return {
        "payload_json": json.dumps({"ticket_id": ticket_id}),
        "delivery_id": "d-test",
        "attempt": str(attempt),
        "not_before_ts": "0.0",
    }


async def wait_for_worker_ack(fake: WorkerRedis) -> None:
    for _ in range(100):
        if fake.acked:
            return
        await asyncio.sleep(0.01)


def reset_worker_state() -> None:
    redis_queue._worker_task = None  # noqa: SLF001
    redis_queue._worker_stop_event = None  # noqa: SLF001


async def sleeping_worker_loop(s: Any, e: Any) -> None:  # noqa: ARG001
    try:
        await asyncio.sleep(100)
    except asyncio.CancelledError:
        return
