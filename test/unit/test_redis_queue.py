from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from test.support.checks import check
from test.support.redis_queue_helpers import FakeRedis as _FakeRedis
from test.support.settings_factory import make_settings
from zammad_pdf_archiver.app.jobs import redis_queue


@dataclass
class _FakeCounter:
    count: int = 0
    label_calls: list[dict[str, str]] = field(default_factory=list)

    def labels(self, **kwargs: str) -> _FakeCounter:
        self.label_calls.append(kwargs)
        return self

    def inc(self) -> None:
        self.count += 1


def test_get_queue_stats_inprocess_disabled(tmp_path) -> None:
    settings = make_settings(
        str(tmp_path),
        overrides={"workflow": {"execution_backend": "inprocess"}},
    )
    stats = redis_queue.asyncio.run(redis_queue.get_queue_stats(settings))
    check(
        not not stats == {"execution_backend": "inprocess", "queue_enabled": False},
        "assertion failed",
    )


def test_ensure_group_replays_existing_backlog() -> None:
    class _GroupRedis:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str, str, bool]] = []

        async def xgroup_create(
            self, stream: str, group: str, **kwargs: Any
        ) -> None:
            group_id = str(kwargs["id"])
            mkstream = bool(kwargs["mkstream"])
            self.calls.append((stream, group, group_id, mkstream))

    fake = _GroupRedis()
    redis_queue.asyncio.run(redis_queue._ensure_group(fake, stream="s", group="g"))  # noqa: SLF001

    check(not not fake.calls == [("s", "g", "0", True)], "assertion failed")


def test_claim_stale_pending_reassigns_messages() -> None:
    class _PendingEntry:
        def __init__(self, message_id: str, consumer: str, idle_ms: int) -> None:
            self.message_id = message_id
            self.consumer = consumer
            self.time_since_delivered = idle_ms
            self.times_delivered = 1

    class _ClaimRedis:
        def __init__(self) -> None:
            self.claim_ids: list[str] = []

        async def xpending_range(self, stream, group, min_id, max_id, count):  # noqa: ANN001, ARG002
            return [
                {
                    "message_id": "1-0",
                    "consumer": "worker-a",
                    "time_since_delivered": 45_000,
                    "times_delivered": 1,
                },
                {
                    "message_id": "2-0",
                    "consumer": "worker-b",
                    "time_since_delivered": 5_000,
                    "times_delivered": 1,
                },
                {
                    "message_id": "3-0",
                    "consumer": "worker-c",
                    "time_since_delivered": 35_000,
                    "times_delivered": 2,
                },
                {
                    "message_id": "4-0",
                    "consumer": "worker-new",
                    "time_since_delivered": 55_000,
                    "times_delivered": 3,
                },
                _PendingEntry("5-0", "worker-d", 50_000),
            ]

        async def xclaim(  # noqa: ANN001
            self, stream, group, consumer, min_idle_ms, message_ids
        ):
            self.claim_ids = list(message_ids)
            return [(message_id, {"payload_json": "{}"}) for message_id in message_ids]

    fake = _ClaimRedis()
    messages = redis_queue.asyncio.run(
        redis_queue._claim_stale_pending(
            fake,
            stream="stream-1",
            group="group-1",
            consumer="worker-new",
            count=10,
            min_idle_ms=30_000,
        )
    )

    check(not not fake.claim_ids == ["1-0", "3-0", "5-0"], "assertion failed")
    check(
        not not messages
        == [
            ("1-0", {"payload_json": "{}"}),
            ("3-0", {"payload_json": "{}"}),
            ("5-0", {"payload_json": "{}"}),
        ],
        "assertion failed",
    )


# ---------------------------------------------------------------------------
# New tests for missing coverage
# ---------------------------------------------------------------------------


def test_get_redis_raises_when_no_redis_url(tmp_path) -> None:
    settings = make_settings(
        str(tmp_path),
        overrides={"workflow": {"execution_backend": "inprocess", "redis_url": ""}},
    )
    with pytest.raises(RuntimeError, match="redis_url"):
        redis_queue.asyncio.run(redis_queue._get_redis(settings))  # noqa: SLF001


def test_process_messages_exception_is_caught(monkeypatch, tmp_path) -> None:
    settings = make_settings(
        str(tmp_path),
        overrides={
            "workflow": {"execution_backend": "redis_queue", "redis_url": "redis://localhost:6379"}
        },
    )
    fake = _FakeRedis()

    async def _failing_handle_envelope(redis, *, settings, envelope):  # noqa: ANN001, ARG001
        raise RuntimeError("boom")

    monkeypatch.setattr(redis_queue, "_handle_envelope", _failing_handle_envelope)  # noqa: SLF001

    messages = [
        (
            "1-0",
            {
                "payload_json": '{"ticket_id": 1}',
                "delivery_id": "d1",
                "attempt": "0",
                "not_before_ts": "0.0",
            },
        )
    ]
    result = redis_queue.asyncio.run(
        redis_queue._process_messages(fake, settings=settings, messages=messages)  # noqa: SLF001
    )
    check(not result is not None, "assertion failed")


def test_stop_queue_worker_no_task_returns_immediately(tmp_path) -> None:
    settings = make_settings(
        str(tmp_path),
        overrides={
            "workflow": {"execution_backend": "redis_queue", "redis_url": "redis://localhost:6379"}
        },
    )

    async def run() -> None:
        redis_queue._worker_task = None  # noqa: SLF001
        redis_queue._worker_stop_event = None  # noqa: SLF001
        await redis_queue.stop_queue_worker(settings)

    redis_queue.asyncio.run(run())
def test_stop_queue_worker_timeout_cancels_task(tmp_path) -> None:
    settings = make_settings(
        str(tmp_path),
        overrides={
            "workflow": {"execution_backend": "redis_queue", "redis_url": "redis://localhost:6379"}
        },
    )

    async def run() -> None:
        stop_event = redis_queue.asyncio.Event()

        async def _forever() -> None:
            await redis_queue.asyncio.sleep(999)

        task = redis_queue.asyncio.create_task(_forever())
        redis_queue._worker_task = task  # noqa: SLF001
        redis_queue._worker_stop_event = stop_event  # noqa: SLF001

        await redis_queue.stop_queue_worker(settings, timeout=0.01)
        check(not not task.done(), "assertion failed")
        check(not not stop_event.is_set(), "assertion failed")
        check(not redis_queue._worker_task is not None, "assertion failed")  # noqa: SLF001
        check(not redis_queue._worker_stop_event is not None, "assertion failed")  # noqa: SLF001

    redis_queue.asyncio.run(run())
