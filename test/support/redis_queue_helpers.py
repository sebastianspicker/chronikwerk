from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from test.support.settings_factory import make_settings
from zammad_pdf_archiver.app.jobs import redis_queue


def redis_queue_settings(tmp_path: Any) -> redis_queue.Settings:
    return make_settings(
        str(tmp_path),
        overrides={
            "workflow": {
                "execution_backend": "redis_queue",
                "redis_url": "redis://localhost/0",
                "queue_stream": "zammad:jobs",
                "queue_group": "archiver",
                "queue_dlq_stream": "zammad:jobs:dlq",
                "queue_retry_max_attempts": 3,
                "queue_retry_backoff_seconds": 1.0,
            }
        },
    )


@dataclass
class FakeRedis:
    """Minimal async Redis stub used by queue unit tests."""

    xadds: list[tuple[str, dict[str, str]]] = field(default_factory=list)
    acked: list[tuple[str, str, str]] = field(default_factory=list)
    deleted: list[tuple[str, str]] = field(default_factory=list)
    dlq_entries: list[tuple[str, dict[str, str]]] = field(default_factory=list)
    xdel_results: list[int] = field(default_factory=list)

    async def xadd(self, stream: str, fields: dict[str, str]) -> str:
        self.xadds.append((stream, fields))
        if stream.endswith(":dlq"):
            self.dlq_entries.append((f"{len(self.dlq_entries) + 1}-0", fields))
        return f"{len(self.xadds)}-0"

    async def xack(self, stream: str, group: str, message_id: str) -> int:
        self.acked.append((stream, group, message_id))
        return 1

    async def xdel(self, stream: str, message_id: str) -> int:
        result = self.xdel_results.pop(0) if self.xdel_results else 1
        if result:
            self.deleted.append((stream, message_id))
        return result

    async def xrange(
        self,
        stream: str,  # noqa: ARG002
        **kwargs: Any,
    ) -> list[tuple[str, dict[str, str]]]:
        count = int(kwargs["count"])
        return self.dlq_entries[:count]


class Counter:
    def __init__(self) -> None:
        self.count = 0

    def inc(self) -> None:
        self.count += 1


def replayable_dlq_entries() -> list[tuple[str, dict[str, str]]]:
    return [
        (
            "1-0",
            {
                "payload_json": json.dumps({"ticket_id": 10}),
                "delivery_id": "d-replay-1",
                "attempt": "4",
                "reason": "retry_exhausted",
            },
        ),
        (
            "2-0",
            {
                "payload_json": json.dumps({"ticket_id": 20}),
                "delivery_id": "",
                "attempt": "2",
                "reason": "permanent_error",
            },
        ),
    ]


def track_replay_enqueue(
    fake: FakeRedis, settings: redis_queue.Settings
) -> tuple[list[dict[str, Any]], Any]:
    enqueue_calls: list[dict[str, Any]] = []

    async def _tracking_enqueue(**kwargs: Any) -> str:
        enqueue_calls.append(kwargs)
        return await fake.xadd(settings.workflow.queue_stream, {"payload_json": "{}"})

    return enqueue_calls, _tracking_enqueue
