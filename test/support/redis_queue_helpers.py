from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from test.support.checks import check
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
class Pipeline:
    redis: FakeRedis
    dels: list[tuple[str, str]] = field(default_factory=list)

    def xdel(self, stream: str, message_id: str) -> Pipeline:
        self.dels.append((stream, message_id))
        return self

    async def execute(self) -> list[int]:
        if self.redis.pipeline_error is not None:
            raise self.redis.pipeline_error
        results = self.redis.pipeline_results
        if results is None:
            results = [1 for _ in self.dels]
        for (stream, message_id), deleted in zip(self.dels, results, strict=False):
            if deleted:
                self.redis.deleted.append((stream, message_id))
        return results


@dataclass
class FakeRedis:
    """Minimal async Redis stub used by queue unit tests."""

    xadds: list[tuple[str, dict[str, str]]] = field(default_factory=list)
    acked: list[tuple[str, str, str]] = field(default_factory=list)
    deleted: list[tuple[str, str]] = field(default_factory=list)
    stream_lengths: dict[str, int] = field(default_factory=dict)
    pending: int = 0
    dlq_entries: list[tuple[str, dict[str, str]]] = field(default_factory=list)
    pipeline_results: list[int] | None = None
    pipeline_error: Exception | None = None
    xdel_results: list[int] = field(default_factory=list)
    xdel_errors: dict[str, Exception] = field(default_factory=dict)

    async def xadd(self, stream: str, fields: dict[str, str]) -> str:
        self.xadds.append((stream, fields))
        self.stream_lengths[stream] = self.stream_lengths.get(stream, 0) + 1
        if stream.endswith(":dlq"):
            self.dlq_entries.append((f"{len(self.dlq_entries) + 1}-0", fields))
        return f"{len(self.xadds)}-0"

    async def xack(self, stream: str, group: str, message_id: str) -> int:
        self.acked.append((stream, group, message_id))
        return 1

    async def xdel(self, stream: str, message_id: str) -> int:
        error = self.xdel_errors.get(message_id)
        if error is not None:
            raise error
        result = self.xdel_results.pop(0) if self.xdel_results else 1
        if result:
            self.deleted.append((stream, message_id))
        return result

    async def xlen(self, stream: str) -> int:
        return self.stream_lengths.get(stream, 0)

    async def xpending(self, stream: str, group: str) -> dict[str, int]:  # noqa: ARG002
        return {"pending": self.pending}

    async def xrange(
        self,
        stream: str,  # noqa: ARG002
        **kwargs: Any,
    ) -> list[tuple[str, dict[str, str]]]:
        count = int(kwargs["count"])
        return self.dlq_entries[:count]

    def pipeline(self, transaction: bool = False) -> Pipeline:  # noqa: ARG002
        return Pipeline(redis=self)

    async def aclose(self) -> None:
        return None


class Counter:
    def __init__(self) -> None:
        self.count = 0

    def inc(self) -> None:
        self.count += 1


def assert_acked_and_deleted(fake: Any, settings: redis_queue.Settings, message_id: str) -> None:
    check(
        not not fake.acked
        == [(settings.workflow.queue_stream, settings.workflow.queue_group, message_id)],
        "assertion failed",
    )
    check(
        not not fake.deleted == [(settings.workflow.queue_stream, message_id)],
        "assertion failed",
    )


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


def stub_retry_enqueue(
    monkeypatch: Any,
    fake: FakeRedis,
    *,
    preserve_payload_json: bool = False,
) -> None:
    async def _stub_enqueue_ticket_job(
        *,
        delivery_id: str | None,
        payload: dict[str, Any],
        settings: redis_queue.Settings,
        attempt: int,
        not_before_ts: float,
        last_error: str | None,
    ) -> str:
        payload_json = (
            json.dumps(payload, separators=(",", ":"), sort_keys=True)
            if preserve_payload_json
            else "{}"
        )
        fields = {
            "payload_json": payload_json,
            "delivery_id": delivery_id or "",
            "attempt": str(attempt),
            "not_before_ts": str(not_before_ts),
            "last_error": last_error or "",
        }
        return await fake.xadd(settings.workflow.queue_stream, fields)

    monkeypatch.setattr(redis_queue, "enqueue_ticket_job", _stub_enqueue_ticket_job)


def track_replay_enqueue(
    fake: FakeRedis, settings: redis_queue.Settings
) -> tuple[list[dict[str, Any]], Any]:
    enqueue_calls: list[dict[str, Any]] = []

    async def _tracking_enqueue(**kwargs: Any) -> str:
        enqueue_calls.append(kwargs)
        return await fake.xadd(settings.workflow.queue_stream, {"payload_json": "{}"})

    return enqueue_calls, _tracking_enqueue
