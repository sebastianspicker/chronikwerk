"""Tests for stream reading, message processing, queue stats, and public API in redis_queue."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock

from test.support.checks import check
from test.support.settings_factory import make_settings
from zammad_pdf_archiver.app.jobs import redis_queue
from zammad_pdf_archiver.app.jobs.process_ticket import ProcessTicketResult

# ---------------------------------------------------------------------------
# Shared fake Redis
# ---------------------------------------------------------------------------


@dataclass
class _FakeRedis:
    """Minimal fake Redis supporting xreadgroup, xadd, xack, xdel, xlen, xpending."""

    xadds: list[tuple[str, dict[str, str]]] = field(default_factory=list)
    acked: list[tuple[str, str, str]] = field(default_factory=list)
    deleted: list[tuple[str, str]] = field(default_factory=list)
    stream_lengths: dict[str, int] = field(default_factory=dict)
    pending_count: int = 0

    # Pre-programmed responses for xreadgroup keyed on the stream id ("0" or ">")
    xreadgroup_responses: dict[str, Any] = field(default_factory=dict)

    # xgroup_create tracking
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

    async def xack(self, stream: str, group: str, message_id: str) -> int:
        self.acked.append((stream, group, message_id))
        return 1

    async def xdel(self, stream: str, message_id: str) -> int:
        self.deleted.append((stream, message_id))
        return 1

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


class _WorkerRedis:
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

    async def xack(self, stream: str, group: str, message_id: str) -> int:
        self.acked.append((stream, group, message_id))
        return 1

    async def xdel(self, stream: str, message_id: str) -> int:
        self.deleted.append((stream, message_id))
        return 1

    async def ping(self) -> None:
        return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_redis_settings(tmp_path: Any, **extra: Any) -> Any:
    overrides: dict[str, Any] = {
        "workflow": {
            "execution_backend": "redis_queue",
            "redis_url": "redis://localhost/0",
        }
    }
    overrides["workflow"].update(extra)
    return make_settings(str(tmp_path), overrides=overrides)


def _valid_raw_fields(ticket_id: int = 42, attempt: int = 0) -> dict[str, str]:
    return {
        "payload_json": json.dumps({"ticket_id": ticket_id}),
        "delivery_id": "d-test",
        "attempt": str(attempt),
        "not_before_ts": "0.0",
    }


async def _wait_for_worker_ack(fake: _WorkerRedis) -> None:
    for _ in range(100):
        if fake.acked:
            return
        await asyncio.sleep(0.01)


# ===========================================================================
# 1. _read_own_pending
# ===========================================================================


class TestReadOwnPending:
    def test_read_own_pending_has_messages(self) -> None:
        messages_data = [("msg-1", _valid_raw_fields()), ("msg-2", _valid_raw_fields(99))]
        fake = _FakeRedis(xreadgroup_responses={"0": [["mystream", messages_data]]})

        result = asyncio.run(
            redis_queue._read_own_pending(  # noqa: SLF001
                fake, stream="mystream", group="grp", consumer="c1", count=10
            )
        )
        check(not not result == messages_data, "assertion failed")

    def test_read_own_pending_empty(self) -> None:
        fake = _FakeRedis(xreadgroup_responses={"0": []})

        result = asyncio.run(
            redis_queue._read_own_pending(  # noqa: SLF001
                fake, stream="mystream", group="grp", consumer="c1", count=10
            )
        )
        check(not not result == [], "assertion failed")


# ===========================================================================
# 2. _read_new_messages
# ===========================================================================


class TestReadNewMessages:
    def test_read_new_messages_has_messages(self) -> None:
        messages_data = [("new-1", _valid_raw_fields())]
        fake = _FakeRedis(xreadgroup_responses={">": [["mystream", messages_data]]})

        result = asyncio.run(
            redis_queue._read_new_messages(  # noqa: SLF001
                fake,
                stream="mystream",
                group="grp",
                consumer="c1",
                count=10,
                block_ms=500,
            )
        )
        check(not not result == messages_data, "assertion failed")

    def test_read_new_messages_timeout(self) -> None:
        # xreadgroup returns None on timeout (no messages available)
        fake = _FakeRedis(xreadgroup_responses={">": None})

        result = asyncio.run(
            redis_queue._read_new_messages(  # noqa: SLF001
                fake,
                stream="mystream",
                group="grp",
                consumer="c1",
                count=10,
                block_ms=500,
            )
        )
        check(not not result == [], "assertion failed")


# ===========================================================================
# 3. _process_messages
# ===========================================================================


class TestProcessMessages:
    def test_process_messages_success(self, monkeypatch, tmp_path) -> None:
        settings = _make_redis_settings(tmp_path)
        fake = _FakeRedis()

        async def _stub_handle(redis, *, settings, envelope):  # noqa: ANN001, ARG001
            return 0.0

        monkeypatch.setattr(redis_queue, "_handle_envelope", _stub_handle)

        messages = [("m-1", _valid_raw_fields())]
        result = asyncio.run(
            redis_queue._process_messages(fake, settings=settings, messages=messages)  # noqa: SLF001
        )
        # Non-positive delays do not request a revisit.
        check(not result is not None, "assertion failed")

    def test_process_messages_decode_error(self, monkeypatch, tmp_path) -> None:
        settings = _make_redis_settings(tmp_path)
        fake = _FakeRedis()

        # Stub record_history_event so it doesn't try to connect to real Redis
        monkeypatch.setattr(redis_queue, "record_history_event", AsyncMock(return_value=True))

        # Malformed message: payload_json is not valid JSON
        malformed_fields: dict[str, str] = {"payload_json": "<<<not json>>>"}
        messages = [("bad-1", malformed_fields)]

        result = asyncio.run(
            redis_queue._process_messages(fake, settings=settings, messages=messages)  # noqa: SLF001
        )

        # Should have pushed to DLQ
        check(
            not not any((stream == settings.workflow.queue_dlq_stream for stream, _ in fake.xadds)),
            "assertion failed",
        )
        # Should have acked and deleted the bad message
        check(not not ("bad-1",) == tuple((mid for _, _, mid in fake.acked)), "assertion failed")
        check(not not any((mid == "bad-1" for _, mid in fake.deleted)), "assertion failed")
        # No pending delay from decode errors
        check(not result is not None, "assertion failed")

    def test_process_messages_returns_min_delay(self, monkeypatch, tmp_path) -> None:
        settings = _make_redis_settings(tmp_path)
        fake = _FakeRedis()

        call_count = 0

        async def _stub_handle(redis, *, settings, envelope):  # noqa: ANN001, ARG001
            nonlocal call_count
            call_count += 1
            # First message has a 5s delay, second has 2s delay
            return 5.0 if call_count == 1 else 2.0

        monkeypatch.setattr(redis_queue, "_handle_envelope", _stub_handle)

        messages = [
            ("m-1", _valid_raw_fields(1)),
            ("m-2", _valid_raw_fields(2)),
        ]
        result = asyncio.run(
            redis_queue._process_messages(fake, settings=settings, messages=messages)  # noqa: SLF001
        )
        # min(5.0, 2.0) == 2.0
        check(not not result == 2.0, "assertion failed")


# ===========================================================================
# 4. get_queue_stats
# ===========================================================================


class TestGetQueueStats:
    def test_queue_stats_redis_backend(self, monkeypatch, tmp_path) -> None:
        settings = _make_redis_settings(tmp_path)
        fake = _FakeRedis(
            stream_lengths={"zammad:jobs": 7, "zammad:jobs:dlq": 2},
            pending_count=3,
        )

        async def _stub_get_redis(_settings: Any) -> _FakeRedis:
            return fake

        monkeypatch.setattr(redis_queue, "_get_redis", _stub_get_redis)

        stats = asyncio.run(redis_queue.get_queue_stats(settings))

        check(not not stats["execution_backend"] == "redis_queue", "assertion failed")
        check(not stats["queue_enabled"] is not True, "assertion failed")
        check(not not stats["queue_depth"] == 7, "assertion failed")
        check(not not stats["dlq_depth"] == 2, "assertion failed")
        check(not not stats["pending"] == 3, "assertion failed")
        check(not "consumer" not in stats, "assertion failed")
        check(not "stream" not in stats, "assertion failed")

    def test_queue_stats_inprocess_backend(self, tmp_path) -> None:
        settings = make_settings(
            str(tmp_path),
            overrides={"workflow": {"execution_backend": "inprocess"}},
        )
        stats = asyncio.run(redis_queue.get_queue_stats(settings))

        check(
            not not stats == {"execution_backend": "inprocess", "queue_enabled": False},
            "assertion failed",
        )


# ===========================================================================
# 5. Public worker lifecycle
# ===========================================================================


class TestPublicWorkerLifecycle:
    def test_start_queue_worker_returns_none_for_inprocess(self, tmp_path) -> None:
        settings = make_settings(
            str(tmp_path),
            overrides={"workflow": {"execution_backend": "inprocess"}},
        )

        async def _run() -> None:
            redis_queue._worker_task = None  # noqa: SLF001
            redis_queue._worker_stop_event = None  # noqa: SLF001
            check(
                not await redis_queue.start_queue_worker(settings) is not None, "assertion failed"
            )

        asyncio.run(_run())

    def test_start_queue_worker_creates_task(self, monkeypatch, tmp_path) -> None:
        settings = _make_redis_settings(tmp_path)

        async def _run() -> asyncio.Task[None] | None:
            redis_queue._worker_task = None  # noqa: SLF001
            redis_queue._worker_stop_event = None  # noqa: SLF001

            async def _fake_worker_loop(s: Any, e: Any) -> None:  # noqa: ARG001
                try:
                    await asyncio.sleep(100)
                except asyncio.CancelledError:
                    return

            monkeypatch.setattr(redis_queue, "_worker_loop", _fake_worker_loop)
            task = await redis_queue.start_queue_worker(settings)
            check(not not task is not None, "assertion failed")
            check(not not isinstance(task, asyncio.Task), "assertion failed")
            await redis_queue.stop_queue_worker(settings, timeout=0.1)
            return task

        asyncio.run(_run())

    def test_stop_queue_worker_signals_stop(self, monkeypatch, tmp_path) -> None:
        settings = _make_redis_settings(tmp_path)

        async def _run() -> None:
            redis_queue._worker_task = None  # noqa: SLF001
            redis_queue._worker_stop_event = None  # noqa: SLF001
            stop_observed = False

            async def _fake_worker_loop(s: Any, stop_event: asyncio.Event) -> None:  # noqa: ARG001
                nonlocal stop_observed
                await stop_event.wait()
                stop_observed = True

            monkeypatch.setattr(redis_queue, "_worker_loop", _fake_worker_loop)
            await redis_queue.start_queue_worker(settings)
            await redis_queue.stop_queue_worker(settings, timeout=2.0)
            check(not not stop_observed, "assertion failed")
            check(not redis_queue._worker_task is not None, "assertion failed")  # noqa: SLF001
            check(not redis_queue._worker_stop_event is not None, "assertion failed")  # noqa: SLF001

        asyncio.run(_run())

    def test_start_queue_worker_reuses_existing(self, monkeypatch, tmp_path) -> None:
        settings = _make_redis_settings(tmp_path)

        async def _run() -> None:
            redis_queue._worker_task = None  # noqa: SLF001
            redis_queue._worker_stop_event = None  # noqa: SLF001

            async def _fake_worker_loop(s: Any, e: Any) -> None:  # noqa: ARG001
                try:
                    await asyncio.sleep(100)
                except asyncio.CancelledError:
                    return

            monkeypatch.setattr(redis_queue, "_worker_loop", _fake_worker_loop)
            task1 = await redis_queue.start_queue_worker(settings)
            task2 = await redis_queue.start_queue_worker(settings)
            check(not task1 is not task2, "assertion failed")
            await redis_queue.stop_queue_worker(settings, timeout=0.1)

        asyncio.run(_run())

    def test_public_worker_consumes_enqueued_message(self, monkeypatch, tmp_path) -> None:
        settings = _make_redis_settings(tmp_path, queue_read_block_ms=100)
        fake = _WorkerRedis()
        processed: list[tuple[str | None, dict[str, Any]]] = []

        async def _fake_get_redis(_settings: Any) -> _WorkerRedis:
            return fake

        async def _fake_process_ticket(
            delivery_id: str | None,
            payload: dict[str, Any],
            _settings: Any,
        ) -> ProcessTicketResult:
            processed.append((delivery_id, payload))
            return ProcessTicketResult(status="processed", ticket_id=payload.get("ticket_id"))

        async def _run() -> None:
            redis_queue._worker_task = None  # noqa: SLF001
            redis_queue._worker_stop_event = None  # noqa: SLF001

            message_id = await redis_queue.enqueue_ticket_job(
                delivery_id="d-public-worker",
                payload={"ticket_id": 42},
                settings=settings,
            )
            check(not not message_id == "1-0", "assertion failed")

            task = await redis_queue.start_queue_worker(settings)
            check(not not task is not None, "assertion failed")
            try:
                await _wait_for_worker_ack(fake)
            finally:
                await redis_queue.stop_queue_worker(settings, timeout=1.0)

            if task is None:
                raise AssertionError("assertion failed")
            check(not not task.done(), "assertion failed")

        monkeypatch.setattr(redis_queue, "_get_redis", _fake_get_redis)
        monkeypatch.setattr(redis_queue, "process_ticket", _fake_process_ticket)

        asyncio.run(_run())

        check(
            not not fake.groups_created
            == [(settings.workflow.queue_stream, settings.workflow.queue_group, "0", True)],
            "assertion failed",
        )
        check(not not processed == [("d-public-worker", {"ticket_id": 42})], "assertion failed")
        check(
            not not fake.acked
            == [(settings.workflow.queue_stream, settings.workflow.queue_group, "1-0")],
            "assertion failed",
        )
        check(not not fake.deleted == [(settings.workflow.queue_stream, "1-0")], "assertion failed")

    def test_aclose_queue_clients_delegates(self, monkeypatch) -> None:
        calls: list[bool] = []

        async def _fake_close_all() -> None:
            calls.append(True)

        monkeypatch.setattr("zammad_pdf_archiver.adapters.redis_pool.close_all", _fake_close_all)

        asyncio.run(redis_queue.aclose_queue_clients())
        check(not not calls == [True], "assertion failed")
